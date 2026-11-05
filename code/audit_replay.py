"""Build versioned audit overlays and recompute statistics, entirely offline."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import socket
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT
AB = ROOT / 'data/human_review'
INTAKE = ROOT / 'data/human_review'
GOLD = {'math_precalculus_test_0039', 'math_precalculus_test_0343'}
VERSIONS = ('audit_original_gold', 'audit_gold_sensitivity')
INPUTS = {}


def deny(*args, **kwargs):
    raise RuntimeError('Offline audit: network connections are blocked.')


socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
socket.getaddrinfo = deny


def sha(p):
    with p.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def track(p):
    rel = p.relative_to(ROOT).as_posix()
    value = sha(p)
    assert rel not in INPUTS or INPUTS[rel] == value
    INPUTS[rel] = value
    return p


def read(p):
    return json.loads(track(p).read_text(encoding='utf-8-sig'))


def rows(p):
    with track(p).open(encoding='utf-8-sig') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def dump(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def line(stream, data):
    stream.write(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')


def ah(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def module(name, p):
    spec = importlib.util.spec_from_file_location(name, track(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def add_target(target, key, material, source):
    if key in target:
        assert target[key]['material'] == material, 'Conflicting material for a trajectory.'
        target[key]['sources'].add(source)
    else:
        target[key] = {'material': material, 'sources': {source}}


def choose(record, adjudicated, version, baseline, failure=False):
    rid = record['review_id']
    if rid in adjudicated:
        if record['task_id'] in GOLD and version == 'audit_original_gold':
            value, origin = baseline, 'gold_addendum_retained_at_baseline'
        else:
            decision = adjudicated[rid]['adjudication_decision']
            value = {'correct': 1, 'incorrect': 0, 'unresolved': None}[decision]
            origin = 'gold_addendum_adjudication' if record['task_id'] in GOLD else 'author_adjudication'
    else:
        decision = record['A']['semantic_decision']
        assert not record['full_review_required'] and record['task_id'] not in GOLD
        assert decision == record['B']['semantic_decision'] and decision in ('correct', 'incorrect')
        value, origin = int(decision == 'correct'), 'definite_AB_agreement'
    return (0, 'execution_zero_policy') if failure else (value, origin)


def self_checks():
    rec = {'review_id': 'test', 'task_id': 'task'}
    decisions = {'test': {'adjudication_decision': 'unresolved'}}
    assert choose(rec, decisions, VERSIONS[0], 1) == (None, 'author_adjudication')
    assert choose(rec, decisions, VERSIONS[0], 1, True) == (0, 'execution_zero_policy')
    rec['task_id'] = sorted(GOLD)[0]
    assert choose(rec, decisions, VERSIONS[0], 1)[0] == 1
    assert choose(rec, decisions, VERSIONS[1], 1)[0] is None
    targets = {}
    add_target(targets, 'k', ('t', 'h'), 'first')
    add_target(targets, 'k', ('t', 'h'), 'second')
    assert len(targets) == 1 and len(targets['k']['sources']) == 2
    try:
        add_target(targets, 'k', ('t', 'different_hash'), 'third')
    except AssertionError:
        pass
    else:
        raise AssertionError('Conflicting answer mapping accepted.')
    return ['unresolved_preserved', 'execution_error_priority', 'gold_baseline_separation',
            'gold_sensitivity_separation', 'crosswalk_deduplication', 'conflicting_mapping_rejected']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ROOT.parent / 'human_audit_reproduction')
    args = ap.parse_args()
    out = args.output.resolve()
    assert not out.exists(), 'Use a fresh output directory.'
    helper = module('audit_preview_helpers', ROOT / 'code/audit_support.py')
    k = module('audit_original_kernel', RELEASE / 'code/original_kernel.py')
    s = module('audit_followup_stats', RELEASE / 'code/followup_stats.py')
    pairing = module('audit_pairing', RELEASE / 'code/pairing.py')
    track(Path(__file__).resolve())
    checks = self_checks()
    method_checks = s.validate_method()
    paired = read(AB / 'paired_records_internal.json')
    material = {(r['task_id'], r['answer_sha256']): r for r in paired}
    assert len(paired) == len(material) == 1216
    pending = {r['review_id']: r for r in paired if r['full_review_required'] or r['task_id'] in GOLD}
    actual = read(INTAKE / 'normalized_decisions_87.json')
    assert sha(INTAKE / 'normalized_decisions_87.json') == read(INTAKE / 'decision_sha256.json')['sha256']
    adjudicated = helper.validate_decisions(actual, pending, date(2026, 11, 5))
    assert len(pending) == 87 and len(material) - len(pending) == 1129
    protected = read(ROOT / 'data/human_review/protected_inputs.json')
    assert all(sha(ROOT / p) == value for p, value in protected.items())
    old_targets, new_targets = {}, {}
    old_unknown = read(AB / 'original_unknown_mapping.json')
    new_unknown = read(AB / 'followup_unknown_mapping.json')
    for name, records in [('original_audit_300', read(ROOT / 'data/human_review/original_audit300.json')),
                          ('original_unknown_695', old_unknown)]:
        for r in records:
            m = r['task_id'], r['answer_sha256']
            assert m in material
            for member in r['members']:
                key = member['run_id'], member['strategy'], r['task_id'], str(member['repeat_id'])
                add_target(old_targets, key, m, name)
    for name, records in [('followup_audit_300', read(ROOT / 'data/human_review/followup_audit300.json')),
                          ('followup_unknown_199', new_unknown)]:
        for r in records:
            m = r['task_id'], r['answer_sha256']
            assert m in material
            for member in r['members']:
                add_target(new_targets, member['run_id'], m, name)
    overlay = {(r['run_id'], r['strategy'], r['task_id'], str(r['repeat_id'])): r
               for r in read(RELEASE / 'data/scoring/author_overlay.json')['entries']}
    pop = read(RELEASE / 'data/population.json')
    replacements = {(r['strategy'], r['task_id'], str(r['repeat_id'])): r
                    for r in rows(RELEASE / 'data/original/replacement7.jsonl')}
    out.mkdir(parents=True)
    dump(out / 'protected_inputs_before.json', protected)
    dump(out / 'adjudications_87.json', actual)
    summaries, primary, repeats = {}, {}, {}
    counts = defaultdict(Counter)
    original_seen, old_found, new_found = set(), set(), set()
    member_counts = Counter()
    changes = {v: Counter() for v in VERSIONS}
    transitions = {v: defaultdict(Counter) for v in VERSIONS}
    replacements_used = set()
    unresolved = []
    all_trace = []
    old_unknown_seen = set()
    old_resource_checks = 0
    with (out / 'original_labels_51000.jsonl').open('w', encoding='utf-8') as labels:
        for case in pop['cases']:
            case_id = case['run_id']
            batch = {v: [] for v in ('frozen', 'historical_author', *VERSIONS)}
            for rel in case['components']:
                for raw in rows(RELEASE / rel):
                    key = raw['strategy'], raw['task_id'], str(raw['repeat_id'])
                    if case['kind'] == 'secondary_backbone' and case['source'] == 'gsm8k' and key in replacements:
                        raw = replacements[key]
                        replacements_used.add(key)
                    fullkey = case_id, *key
                    assert fullkey not in original_seen
                    original_seen.add(fullkey)
                    baseline = raw
                    if fullkey in overlay:
                        prior = overlay[fullkey]
                        assert ah(str(raw.get('final_answer') or '').strip()) == prior['answer_sha256']
                        baseline = dict(raw, success=bool(prior['author_outcome']), needs_review=False)
                    raw_value, baseline_value = k.result(raw), k.result(baseline)
                    if baseline_value is None:
                        old_unknown_seen.add(fullkey)
                        assert fullkey in old_targets and 'original_unknown_695' in old_targets[fullkey]['sources']
                    rec = None
                    if fullkey in old_targets:
                        target = old_targets[fullkey]
                        assert ah(str(raw.get('final_answer') or '')) == target['material'][1]
                        rec = material[target['material']]
                        old_found.add(fullkey)
                        member_counts[rec['review_id']] += 1
                    audit_values, origins = {}, {}
                    for version in VERSIONS:
                        value, origin = (baseline_value, 'unmapped_baseline') if rec is None else choose(
                            rec, adjudicated, version, baseline_value, bool(raw.get('error') or not str(raw.get('final_answer') or '').strip()))
                        updated = dict(baseline, success=None if value is None else bool(value), needs_review=value is None)
                        assert k.result(updated) == value
                        assert {p: x for p, x in updated.items() if p not in ('success', 'needs_review')} == {p: x for p, x in raw.items() if p not in ('success', 'needs_review')}
                        batch[version].append(updated)
                        audit_values[version], origins[version] = value, origin
                        transitions[version]['original'][f'{baseline_value}->{value}'] += 1
                        changes[version]['original'] += int(value != baseline_value)
                    batch['frozen'].append(raw)
                    batch['historical_author'].append(baseline)
                    ledger = {'key': list(fullkey), 'source': case['source'], 'difficulty': raw['difficulty'],
                              'task_id': raw['task_id'], 'answer_sha256': ah(str(raw.get('final_answer') or '')),
                              'frozen': raw_value, 'historical_author': baseline_value, **audit_values,
                              'review_id': rec['review_id'] if rec else None, 'origins': origins}
                    line(labels, ledger)
                    if rec:
                        all_trace.append(dict(ledger, family='original', crosswalk_sources=sorted(target['sources'])))
                    if any(value is None for value in audit_values.values()):
                        unresolved.append(dict(ledger, family='original'))
            assert len(batch['frozen']) == case['planned_rows']
            summaries[case_id] = {v: k.summary(rr) for v, rr in batch.items()}
            for version, rr in batch.items():
                counts[version].update(str(k.result(row)) for row in rr)
                for strategy, summary in summaries[case_id][version].items():
                    ignore = {'correct', 'incorrect', 'unknown', 'accuracy_bounds'}
                    assert {p: x for p, x in summary.items() if p not in ignore} == {p: x for p, x in summaries[case_id]['frozen'][strategy].items() if p not in ignore}
                    old_resource_checks += 1
            if case['kind'] in ('primary', 'secondary_backbone'):
                primary[case_id] = {}
                for version, rr in batch.items():
                    pp = k.pairs(rr, 'adaptive', 'plan_reflect')
                    bounds = k.bounds(pp)
                    adapted = [{'difficulty': a['difficulty'], 'a': {'bucket': 'unknown' if k.result(a) is None else 'known', 'value': k.result(a)},
                                'b': {'bucket': 'unknown' if k.result(b) is None else 'known', 'value': k.result(b)}} for a, b in pp]
                    independently = s.cp_envelope(adapted)
                    helper.compare(bounds['confidence_interval'], independently['confidence_interval'])
                    helper.compare(bounds['difference_bounds'], independently['difference_bounds'])
                    primary[case_id][version] = {'primary_stratified_cp': bounds, 'unknown_zero_cp': s.cp_envelope_zero(adapted),
                        'unknown_zero_newcombe10': s.score_summary(adapted, 'zero'), 'known_labels_newcombe10': s.score_summary(adapted, 'known')}
                expected = read(RELEASE / 'expected/original_reports.json')[case_id]
                helper.compare(primary[case_id]['frozen']['primary_stratified_cp'], expected['original_quality'])
                helper.compare(primary[case_id]['historical_author']['primary_stratified_cp'], expected['quality'])
            if case['kind'] == 'repeats':
                repeats[case_id] = {}
                for version, rr in batch.items():
                    pp = k.pairs(rr, 'adaptive', 'plan_reflect')
                    result = {}
                    for j, assignment in enumerate(('low_assignment', 'high_assignment')):
                        assigned = [(dict(a, _v=j if k.result(a) is None else k.result(a)),
                                     dict(b, _v=1-j if k.result(b) is None else k.result(b))) for a, b in pp]
                        result[assignment] = k.bootstrap(assigned, lambda row: row['_v'])
                    # Per-arm consistency uses the original fixed task set and three repeats.
                    by = defaultdict(list)
                    for row in rr:
                        by[(row['strategy'], row['task_id'])].append(k.result(row))
                    result['within_task_consistency'] = {}
                    for strategy in sorted({row['strategy'] for row in rr}):
                        triples = [vv for (st, task), vv in by.items() if st == strategy]
                        assert len(triples) == 100 and all(len(vv) == 3 for vv in triples)
                        known = [vv for vv in triples if None not in vv]
                        identical = sum(len(set(vv)) == 1 for vv in known)
                        result['within_task_consistency'][strategy] = {'fixed_tasks': len(triples), 'three_known_tasks': len(known),
                            'identical_tasks': identical, 'conditional_identical_rate': identical / len(known) if known else None}
                    repeats[case_id][version] = result
            print('Rebuilt original case: ' + case_id, flush=True)
    assert len(original_seen) == 51000 and old_found == set(old_targets)
    assert len(old_unknown_seen) == 1731 and len(old_unknown) == 695 and len(replacements_used) == 7
    follow = list(rows(RELEASE / 'data/followup/main/scored.jsonl')) + list(rows(RELEASE / 'data/followup/repeats/scored.jsonl'))
    # Recheck answer hashes against full saved trajectories, not only scored metadata.
    answers = {r['run_id']: r for phase in ('main', 'repeats') for r in rows(RELEASE / f'data/followup/{phase}/trajectories.jsonl')}
    assert len(answers) == len(follow) == 6400 and len({r['run_id'] for r in follow}) == 6400
    new_batch = {v: [] for v in VERSIONS}
    new_unknown_seen = set()
    with (out / 'followup_labels_6400.jsonl').open('w', encoding='utf-8') as labels:
        for row in follow:
            answer = answers[row['run_id']]
            assert row['task_id'] == answer['task_id'] and row['answer_sha256'] == ah(str(answer.get('final_answer') or ''))
            if row['value'] is None:
                new_unknown_seen.add(row['run_id'])
                assert row['run_id'] in new_targets and 'followup_unknown_199' in new_targets[row['run_id']]['sources']
            rec = None
            if row['run_id'] in new_targets:
                target = new_targets[row['run_id']]
                assert (row['task_id'], row['answer_sha256']) == target['material']
                rec = material[target['material']]
                new_found.add(row['run_id'])
                member_counts[rec['review_id']] += 1
            audit_values, origins = {}, {}
            for version in VERSIONS:
                value, origin = (row['value'], 'unmapped_baseline') if rec is None else choose(
                    rec, adjudicated, version, row['value'], row['bucket'] == 'zero_by_policy')
                updated = dict(row, value=value, known=value is not None,
                               bucket='unknown' if value is None else ('zero_by_policy' if row['bucket'] == 'zero_by_policy' else 'known'))
                # The reason must follow the new label rather than retain an obsolete scorer diagnosis.
                updated['reason'] = row['reason'] if rec is None or origin == 'gold_addendum_retained_at_baseline' else origin
                assert {p: x for p, x in updated.items() if p not in ('value', 'known', 'bucket', 'reason')} == {p: x for p, x in row.items() if p not in ('value', 'known', 'bucket', 'reason')}
                new_batch[version].append(updated)
                audit_values[version], origins[version] = value, origin
                transitions[version]['followup'][f'{row["value"]}->{value}'] += 1
                changes[version]['followup'] += int(row['value'] != value)
            ledger = {'key': row['run_id'], 'pair_id': row['pair_id'], 'task_id': row['task_id'], 'source': row['source'],
                      'difficulty': row['difficulty'], 'strategy': row['strategy'], 'repeat_id': row['repeat_id'],
                      'answer_sha256': row['answer_sha256'], 'baseline': row['value'], **audit_values,
                      'review_id': rec['review_id'] if rec else None, 'origins': origins}
            line(labels, ledger)
            if rec:
                all_trace.append(dict(ledger, family='followup', crosswalk_sources=sorted(target['sources'])))
            if any(value is None for value in audit_values.values()):
                unresolved.append(dict(ledger, family='followup'))
    assert new_found == set(new_targets) and len(new_unknown) == 199 and len(new_unknown_seen) == 321
    assert set(member_counts) == {r['review_id'] for r in paired}
    queue = list(rows(RELEASE / 'data/followup/formal_queue.jsonl'))
    follow_results = {}
    for version, rr in [('baseline', follow), *new_batch.items()]:
        pp = pairing.pair_outcomes(rr, queue=queue)
        assert len(pp) == 3200 and all(p['a'] is not None and p['b'] is not None for p in pp)
        report = s.analyze(pp)
        report['per_repeat_paired_cp'] = {src: {str(i): s.cp_envelope([p for p in pp if p['source'] == src and int(p['repeat_id']) == i]) for i in (1, 2)} for src in ('gsm8k', 'math')}
        report['label_counts_by_phase_source_strategy'] = {}
        for phase in sorted({row['phase'] for row in rr}):
            for src in ('gsm8k', 'math'):
                for strategy in ('adaptive', 'plan_reflect'):
                    values = [r['value'] for r in rr if r['phase'] == phase and r['source'] == src and r['strategy'] == strategy]
                    report['label_counts_by_phase_source_strategy'][f'{phase}/{src}/{strategy}'] = {'n': len(values),
                        'correct': values.count(1), 'incorrect': values.count(0), 'unknown': values.count(None),
                        'accuracy_bounds': [values.count(1)/len(values), (values.count(1)+values.count(None))/len(values)]}
        report['all_labels'] = dict(Counter(str(r['value']) for r in rr))
        follow_results[version] = report
        print('Rebuilt followup version: ' + version, flush=True)
    expected = read(RELEASE / 'expected/followup_combined.json')
    for key in ('sources', 'repeat_consistency', 'all_sources_pooled', 'per_repeat_paired_cp'):
        helper.compare(follow_results['baseline'][key], expected[key])
    for version in VERSIONS:
        for src in ('gsm8k', 'math'):
            assert follow_results[version]['sources'][src]['resources'] == follow_results['baseline']['sources'][src]['resources']
    # Reuse is explicit and constrained to declared members, across source packages.
    membership = [{'review_id': rec['review_id'], 'task_id': rec['task_id'], 'answer_sha256': rec['answer_sha256'],
                   'source_part': rec['source_part'], 'A': rec['A'], 'B': rec['B'],
                   'adjudication': adjudicated.get(rec['review_id']), 'mapped_trajectories': member_counts[rec['review_id']]}
                  for rec in paired]
    dump(out / 'material_decisions_1216.json', membership)
    dump(out / 'mapped_overlay_trace.json', all_trace)
    dump(out / 'remaining_unknown_trajectories.json', unresolved)
    dump(out / 'original_case_summaries.json', summaries)
    dump(out / 'original_primary_statistics.json', primary)
    dump(out / 'original_repeat_statistics.json', repeats)
    dump(out / 'followup_statistics.json', follow_results)
    dump(out / 'label_transitions.json', {v: {family: dict(cc) for family, cc in fam.items()} for v, fam in transitions.items()})
    dump(out / 'audit_policy.json', {'status': 'VERSIONED_POST_TEST_HUMAN_AUDIT', 'versions': {
        'audit_original_gold': '1129 definite AB agreements plus 74 adjudications; 13 gold-addendum materials retain historical baseline labels.',
        'audit_gold_sensitivity': 'All 1216 materials; adds 13 gold-addendum decisions using the two reviewed complete solution sets.'},
        'core_adjudications': 74, 'gold_addendum_adjudications': 13, 'unresolved_adjudications': 3,
        'adjudicator_as_submitted': 'Author', 'source': 'User-submitted review; original source and AI-use confirmations are retained, no new independence claim.',
        'matching': 'Only existing crosswalk members; exact task and untrimmed final-answer SHA256; deduplicate overlapping crosswalk memberships.',
        'scope': 'Final-answer semantic labels, not a correctness audit of every saved derivation.',
        'original_base': 'Historical author overlay, with frozen version separately reported.', 'gold_files_modified': False,
        'raw_score_files_modified': False, 'original_margin': -0.03, 'new_model_calls': 0,
        'kappa': 'Pre-adjudication first-round values remain unchanged; not recalculated from adjudications.'})
    assert all(sha(ROOT / p) == value for p, value in protected.items())
    assert all(sha(ROOT / p) == value for p, value in INPUTS.items())
    dump(out / 'input_sha256.json', INPUTS)
    dump(out / 'protected_inputs_after.json', protected)
    import numpy, scipy
    dump(out / 'environment.json', {'python': sys.version, 'numpy': numpy.__version__, 'scipy': scipy.__version__,
        'network_connections_blocked': True, 'original_bootstrap_seed': 20261014,
        'followup_bootstrap_seed': s.SEED, 'bootstrap_draws': 2000, 'absolute_float_tolerance': 1e-10})
    verification = {'status': 'BUILD_COMPLETE_AWAITING_INDEPENDENT_VERIFICATION', 'plan_steps': ['08', '09'],
        'original_records': len(original_seen), 'followup_records': len(follow), 'original_cases': len(summaries),
        'original_mapped_trajectories': len(old_targets), 'followup_mapped_trajectories': len(new_targets),
        'original_unknown_groups_covered': len(old_unknown), 'original_unknown_trajectories_covered': len(old_unknown_seen),
        'followup_unknown_groups_covered': len(new_unknown), 'followup_unknown_trajectories_covered': len(new_unknown_seen),
        'all_materials_mapped': len(member_counts), 'original_overlap_memberships_deduplicated': sum(len(t['sources'])-1 for t in old_targets.values()),
        'followup_overlap_memberships_deduplicated': sum(len(t['sources'])-1 for t in new_targets.values()),
        'label_changes_vs_historical_baseline': {v: dict(cc) for v, cc in changes.items()},
        'original_label_counts': {v: dict(cc) for v, cc in counts.items()},
        'followup_label_counts': {v: follow_results[v]['all_labels'] for v in follow_results},
        'method_examples_passed': len(method_checks), 'policy_checks_passed': checks,
        'independent_primary_CP_comparisons': len(primary)*4, 'original_resource_summary_checks': old_resource_checks,
        'all_baseline_statistics_reproduced': True, 'followup_resources_unchanged': True,
        'replacement_records_used': len(replacements_used), 'protected_entries_unchanged': len(protected),
        'new_model_calls': 0, 'raw_score_files_modified': 0, 'manuscript_changes': 0, 'public_release_performed': False}
    dump(out / 'verification.json', verification)
    dump(out / 'artifact_manifest.json', {p.relative_to(out).as_posix(): {'bytes': p.stat().st_size, 'sha256': sha(p)}
        for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'artifact_manifest.json'})
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
