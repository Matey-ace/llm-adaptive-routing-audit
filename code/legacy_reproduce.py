"""Portable offline replay of the SNCS audit release. Never imports collectors."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import socket
import sys
import time

sys.dont_write_bytecode = True
# Block connections before importing any supplied study code. No API credential is needed.
def deny_network(*args, **kwargs):
    raise RuntimeError('Network access is disabled in this offline reproduction process')
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
socket.getaddrinfo = deny_network

def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def rows(p):
    with Path(p).open(encoding='utf-8-sig') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def dump(p, v):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def key(r):
    return r['strategy'], r['task_id'], str(r['repeat_id'])

def answer_sha(r, trimmed=False):
    s = str(r.get('final_answer') or '')
    return hashlib.sha256((s.strip() if trimmed else s).encode()).hexdigest()

def original_case(root, spec):
    replacement = {key(r):r for r in rows(root/'data/original/replacement7.jsonl')}
    corrected_case = spec['kind']=='secondary_backbone' and spec['source']=='gsm8k'
    seen=set(); nreplaced=0; strategies=set()
    for rel in spec['components']:
        for r in rows(root/rel):
            k=key(r)
            assert k not in seen, ('duplicate trajectory key',spec['run_id'],k)
            seen.add(k)
            strategies.add(r['strategy'])
            if corrected_case and k in replacement:
                r=replacement[k]; nreplaced+=1
            yield r
    assert len(seen)==spec['planned_rows'], ('population mismatch',spec['run_id'])
    expected={(s,t,f'r{i}') for s in strategies for t in spec['frozen_task_ids'] for i in range(spec['frozen_repeats'])}
    assert seen==expected,('frozen task/repeat coverage',spec['run_id'])
    if corrected_case: assert nreplaced==7

def overlays(root):
    return {(r['run_id'],*key(r)):r for r in read(root/'data/scoring/author_overlay.json')['entries']}

def adjudicate(r, rid, overlay):
    e=overlay.get((rid,*key(r)))
    if e is None: return r
    assert answer_sha(r,True)==e['answer_sha256'], ('overlay answer hash',rid,key(r))
    return dict(r,success=bool(e['author_outcome']),needs_review=False,
                score_reason='author_case_adjudication_'+e['finding'])

def compare(actual, expected, path='', tol=1e-10):
    """Compare expected scientific fields; extra provenance fields are permitted."""
    if isinstance(expected,dict):
        assert isinstance(actual,dict),path
        for k,v in expected.items():
            assert k in actual,(path,k,'missing')
            compare(actual[k],v,path+'/'+str(k),tol)
    elif isinstance(expected,list):
        assert isinstance(actual,list) and len(actual)==len(expected),(path,'length')
        for i,(a,b) in enumerate(zip(actual,expected)):compare(a,b,path+'/'+str(i),tol)
    elif isinstance(expected,bool) or expected is None:
        assert actual is expected,(path,actual,expected)
    elif isinstance(expected,(int,float)):
        assert isinstance(actual,(int,float)) and not isinstance(actual,bool),(path,'type')
        assert actual==expected if isinstance(expected,int) else math.isclose(actual,expected,rel_tol=0,abs_tol=tol),(path,actual,expected)
    else: assert actual==expected,(path,actual,expected)

def verify(root, out):
    manifest=read(root/'MANIFEST.json')
    assert manifest['status']=='local_review_candidate'
    verified=0
    for e in manifest['files']:
        p=(root/e['path']).resolve();p.relative_to(root.resolve())
        assert p.is_file() and p.stat().st_size==e['bytes'],('missing/size',e['path'])
        assert sha(p)==e['sha256'],('hash mismatch',e['path'])
        verified+=1
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    assert actual=={e['path'] for e in manifest['files']}|{'MANIFEST.json'},'Unexpected or missing files'
    pop=read(root/'data/population.json'); tasks=read(root/'data/tasks.json')
    physical=0
    for e in pop['original_components']:
        n=sum(1 for _ in rows(root/e['path']));assert n==e['rows'];physical+=n
    assert physical==51007
    overlay=overlays(root);assert len(overlay)==118
    found=set();counts=Counter();frozen=Counter();adjud=Counter();attempts=0;tokens=0;failed=0
    for spec in pop['cases']:
        n=0
        for r in original_case(root,spec):
            assert r['source']==spec['source'] and r['split']=='test'
            assert r['task_id'] in tasks and tasks[r['task_id']]['split']=='test'
            k=(spec['run_id'],*key(r));assert k not in found;found.add(k)
            counts[spec['kind']]+=1;n+=1
            v=0 if r.get('error') or not str(r.get('final_answer') or '').strip() else None if r.get('needs_review') or r.get('success') is None else int(bool(r['success']))
            frozen[str(v)]+=1
            a=adjudicate(r,spec['run_id'],overlay)
            av=0 if a.get('error') or not str(a.get('final_answer') or '').strip() else None if a.get('needs_review') or a.get('success') is None else int(bool(a['success']))
            adjud[str(av)]+=1
            attempts+=r['request_audit_summary']['recorded_request_attempts']
            tokens+=sum(u.get('total_tokens',0) for u in r.get('usage_records',[]))
            failed+=len(r.get('failed_attempts') or [])
        assert n==spec['planned_rows']
        print('verified',spec['run_id'],n,flush=True)
    assert len(found)==51000 and attempts==124919 and tokens==77303004
    # Counts of failure attempts are derived from actual audit events, not a list-shape assumption.
    assert frozen['None']==1734 and adjud['None']==1731 and adjud['1']-frozen['1']==74
    newkeys=set();newtasks=set();followup_counts={}
    queue=list(rows(root/'data/followup/formal_queue.jsonl'))
    queue_by_run={r['run_id']:r for r in queue}
    assert len(queue)==len(queue_by_run)==6400
    for phase,n in [('main',4000),('repeats',2400)]:
        scored={r['run_id']:r for r in rows(root/f'data/followup/{phase}/scored.jsonl')}
        assert len(scored)==n
        arm=Counter();pairs=defaultdict(set);ids=set()
        for r in rows(root/f'data/followup/{phase}/trajectories.jsonl'):
            assert r['run_id'] not in newkeys;newkeys.add(r['run_id']);newtasks.add(r['task_id']);ids.add(r['run_id'])
            s=scored[r['run_id']]; assert s['answer_sha256']==answer_sha(r),('new answer hash',r['run_id'])
            for f in ['task_id','source','difficulty','strategy','repeat_id','pair_id']:assert s[f]==r[f],(r['run_id'],f)
            q=queue_by_run[r['run_id']]
            for f in ['task_id','source','difficulty','strategy','repeat_id','pair_id']:assert q[f]==r[f],('queue mismatch',r['run_id'],f)
            assert r['task_id'] in tasks
            arm[(r['source'],r['strategy'])]+=1
            pairs[(r['source'],r['task_id'],int(r['repeat_id']))].add(r['strategy'])
        assert ids==set(scored) and len(ids)==n
        assert all(v=={'adaptive','plan_reflect'} for v in pairs.values())
        followup_counts[phase]={'trajectories':n,'pairs':len(pairs),'distinct_tasks':len({k[1] for k in pairs})}
    assert newkeys==set(queue_by_run) and len(newtasks)==2000
    exclusions=list(rows(root/'data/original/excluded_misroute7.jsonl'));assert len(exclusions)==7
    report={'passed':True,'files_verified':verified,'original_physical_records':physical,'original_formal_records':len(found),
        'original_request_attempts':attempts,'original_known_tokens':tokens,'original_frozen_labels':dict(frozen),
        'original_adjudicated_labels':dict(adjud),'author_overlay_entries':118,'excluded_misroutes':7,
        'followup':followup_counts,'new_model_calls':0,'human_ratings':0,'network_connections_blocked':True}
    dump(out/'verification.json',report);return report

PRIMARY=['formal_gsm8k_primary_20261014','formal_math_primary_20261014','formal_gsm8k_qwen_20261014','formal_math_qwen_20261014']
LABELS=['DS GSM8K','DS MATH','Qwen GSM8K','Qwen MATH']

def original_analyses(root,out):
    from code import original_kernel as k
    from code import sensitivity_kernel as s
    pop=read(root/'data/population.json'); overlay=overlays(root)
    archive=read(root/'expected/original_reports.json');results={};primary_cache={};sens=[]
    for spec in pop['cases']:
        rid=spec['run_id'];raw=list(original_case(root,spec));adj=[adjudicate(r,rid,overlay) for r in raw]
        kind=spec['kind'];quality={}; summaries={}
        if kind=='primary':primary_cache[spec['source']]={'frozen':raw,'adjudicated':adj}
        requests=None
        for scoring, rr in [('frozen',raw),('adjudicated',adj)]:
            a,b=('adaptive','vote_k') if kind.startswith('budget') else ('adaptive','plan_reflect')
            compared=rr
            if kind.startswith('ablation_'):
                a,b='adaptive_variant','adaptive_primary'
                compared=[dict(r,strategy=a) for r in rr]+[dict(r,strategy=b) for r in primary_cache[spec['source']][scoring] if r['strategy']=='adaptive']
            pp=k.pairs(compared,a,b)
            if kind=='repeats':
                assigned=[]
                for j in (0,1):
                    pairs=[(dict(x,_v=j if k.result(x) is None else k.result(x)),dict(y,_v=1-j if k.result(y) is None else k.result(y))) for x,y in pp]
                    assigned.append(k.bootstrap(pairs,lambda r:r['_v']))
                q=dict(zip(['low_assignment','high_assignment'],assigned))
            else:q=k.bounds(pp)
            quality[scoring]=q;summaries[scoring]=k.summary(rr)
            ref=archive[rid]
            compare(q,ref['original_quality' if scoring=='frozen' else 'quality'],rid+'/'+scoring+'/quality')
            if scoring=='adjudicated':
                compare(summaries[scoring],ref['strategies'],rid+'/strategies')
                requests=k.bootstrap(pp,lambda r:r.get('request_audit_summary',{}).get('recorded_request_attempts'))
                compare(requests,ref['request_bootstrap'],rid+'/requests')
            if rid in PRIMARY:
                pp2=[(dict(a,answer=str(a.get('final_answer') or ''),error=bool(a.get('error'))),dict(b,answer=str(b.get('final_answer') or ''),error=bool(b.get('error')))) for a,b in pp]
                completed=[(a,b) for a,b in pp2 if not a['error'] and not b['error'] and a['answer'].strip() and b['answer'].strip()]
                zeros=[(dict(a,success=0 if s.outcome(a) is None else s.outcome(a),needs_review=False),dict(b,success=0 if s.outcome(b) is None else s.outcome(b),needs_review=False)) for a,b in pp2]
                sens.append({'run_id':rid,'scoring':scoring,'primary_reproduced':s.cp_envelope(pp2),
                    'full_unknown_zero_cp':s.cp_envelope(zeros),'full_unknown_zero_score':s.score_summary(pp2,'zero'),
                    'known_labels_score':s.score_summary(pp2,'known'),'complete_execution_cp':s.cp_envelope(completed),
                    'complete_execution_known_score':s.score_summary(completed,'known'),'complete_execution_excluded_pairs':1000-len(completed)})
        results[rid]={'records':len(raw),'kind':kind,'source':spec['source'],'quality':quality,'strategies':summaries,'request_bootstrap':requests}
        print('analyzed',rid,flush=True)
    compare(sens,read(root/'expected/paired_sensitivity.json'),'original_sensitivity')
    dump(out/'original_analysis.json',results);dump(out/'original_sensitivity.json',sens)
    return results,sens

def followup_analyses(root,out):
    from code import followup_stats as s
    from code.pairing import pair_outcomes
    main=list(rows(root/'data/followup/main/scored.jsonl'));rep=list(rows(root/'data/followup/repeats/scored.jsonl'))
    queue=list(rows(root/'data/followup/formal_queue.jsonl'))
    mp=pair_outcomes(main,queue=[r for r in queue if int(r['repeat_id'])==0]);cp=pair_outcomes(main+rep,queue=queue)
    a=s.analyze(mp);b=s.analyze(cp)
    b['per_repeat_paired_cp']={source:{str(rep):s.cp_envelope([p for p in cp if p['source']==source and int(p['repeat_id'])==rep]) for rep in (1,2)} for source in ('gsm8k','math')}
    expected_main=read(root/'expected/followup_main.json');expected_combined=read(root/'expected/followup_combined.json')
    # Ignore collection-time commentary and extra provenance, compare every scientific analysis field.
    # The main-only historical file deliberately carried a pending-status comment,
    # not a numerical repeat summary. Repeated-run evidence is compared below.
    for k in ['sources','all_sources_pooled','seed','draws','margin']:compare(a[k],expected_main[k],'followup_main/'+k)
    a['repeat_consistency']={'status':'not_interpreted_in_main_only_replay','reason':'See combined repeat analysis; historical main file was finalized before additional repeats.'}
    for k in ['sources','repeat_consistency','all_sources_pooled','per_repeat_paired_cp']:compare(b[k],expected_combined[k],'followup_combined/'+k)
    arms={}
    for source in ('gsm8k','math'):
        arms[source]={}
        for st in ('adaptive','plan_reflect'):
            rr=[r for r in main if r['source']==source and r['strategy']==st]
            c=Counter(r['value'] for r in rr)
            arms[source][st]={'n':len(rr),'correct':c[1],'incorrect_including_empty_errors':c[0],'unknown':c[None],
                'accuracy_bounds':[c[1]/len(rr),(c[1]+c[None])/len(rr)],
                'mean_request_attempts':sum(r['meter_summary']['n_calls'] for r in rr)/len(rr),
                'known_total_tokens':sum(r['meter_summary']['total_tokens'] for r in rr)}
    compare(arms,read(root/'expected/followup_arm_counts.json'),'followup_arms')
    dump(out/'followup_main_analysis.json',a);dump(out/'followup_combined_analysis.json',b);dump(out/'followup_arm_counts.json',arms)
    return a,b,arms

def signed(x,places=2,scale=1):
    return f'{0 if abs(x)<1e-12 else x*scale:+.{places}f}'.replace('-','−')
def ci(x,scale=100,places=2):return '['+', '.join(signed(v,places,scale) for v in x)+']'
def point(x):return signed(x[0],2,100) if abs(x[0]-x[1])<1e-10 else ci(x)

def publication_tables(root,out,orig,sens,main,combined,arms):
    expected=read(root/'expected/manuscript_tables.json');tables={}
    # Composition is computed from actual formal populations, preserving publication wording.
    n=Counter()
    for rid,r in orig.items():
        label='Qwen' if r['kind']=='secondary_backbone' else 'DS'
        group='budgets' if r['kind'].startswith('budget') else 'ablations' if r['kind'].startswith('ablation') else r['kind']
        n[(label,group)]+=r['records']
    t=[r[:] for r in expected['1']]
    amounts=[n[('DS','primary')],n[('DS','budgets')],n[('DS','ablations')],n[('DS','repeats')],n[('Qwen','secondary_backbone')],sum(n.values())]
    for row,v in zip(t[1:],amounts):row[-1]=f'{v:,}'
    tables['1']=t
    t=[expected['2'][0]]
    for scoring,label in [('frozen','Frozen'),('adjudicated','Adjudicated')]:
        for name,rid in zip(LABELS,PRIMARY):
            r=orig[rid];q=r['quality'][scoring]
            t.append([name,label,point(q['difference_bounds']),ci(q['confidence_interval']),str(q['uncertain_pairs']),f"{r['request_bootstrap']['difference_a_minus_b']:.3f}".replace('-','−')])
    tables['2']=t
    tables['3']=[expected['3'][0]]+[[label]+[f"{orig[rid]['strategies']['adjudicated'][st]['correct']} / {orig[rid]['strategies']['adjudicated'][st]['unknown']}" for rid in PRIMARY] for st,label in [('direct','Direct'),('planning','Planning'),('plan_reflect','Plan+Reflect'),('adaptive','Adaptive'),('vote_k','Vote')]]
    t=[expected['4'][0]]
    for source in ('gsm8k','math'):
        for budget in (2,3,4):
            r=orig[f'formal_{source}_budget{budget}_20261014'];q=r['quality']['adjudicated']
            t.append([source.upper(),str(budget),point(q['difference_bounds']),ci(q['confidence_interval']),f"{r['request_bootstrap']['difference_a_minus_b']:.3f}".replace('-','−')])
    tables['4']=t;t=[expected['5'][0]]
    for source in ('gsm8k','math'):
        q=orig[f'formal_{source}_repeats_20261014']['quality']['adjudicated'];lo,hi=q['low_assignment'],q['high_assignment']
        t.append([source.upper(),point([lo['difference_a_minus_b'],hi['difference_a_minus_b']]),ci(lo['descriptive_percentile95']),ci(hi['descriptive_percentile95'])])
    tables['5']=t;t=[expected['6'][0]]
    for label,rid in zip(LABELS,PRIMARY):
        ss=orig[rid]['strategies']['adjudicated'];a,b=ss['adaptive'],ss['plan_reflect']
        t.append([label,f"{b['mean_recorded_attempts']:.3f}",f"{a['mean_recorded_attempts']:.3f}",f"{b['known_usage_tokens']:,}",f"{a['known_usage_tokens']:,}"])
    tables['6']=t;t7=[expected['7'][0]];t8=[expected['8'][0]]
    for label,rid in zip(LABELS,PRIMARY):
        r=next(r for r in sens if r['run_id']==rid and r['scoring']=='frozen');e=r['complete_execution_cp']
        t7.append([label,ci(r['full_unknown_zero_cp']['confidence_interval']),ci(r['full_unknown_zero_score']['newcombe10_95']),str(r['known_labels_score']['n']),ci(r['known_labels_score']['newcombe10_95'])])
        t8.append([label,str(e['n']),str(e['unknown_pairs']),ci(e['confidence_interval'])])
    tables['7']=t7;tables['8']=t8;t9=[expected['9'][0]];t10=[expected['10'][0]];t11=[expected['11'][0]]
    for source in ('gsm8k','math'):
        aa,bb=arms[source]['adaptive'],arms[source]['plan_reflect'];q=main['sources'][source]['primary_stratified_cp']
        trip=lambda x:f"{x['correct']} / {x['incorrect_including_empty_errors']} / {x['unknown']}"
        t9.append([source.upper(),trip(aa),trip(bb),point(q['difference_bounds']),ci(q['confidence_interval'])])
        rr=main['sources'][source]['resources'];a,b=rr['n_calls'],rr['total_tokens']
        t10.append([source.upper(),signed(a['observed_mean_adaptive_minus_plan_reflect'],3),ci(a['cluster_bootstrap_95'],1,3),signed(b['observed_mean_adaptive_minus_plan_reflect']),ci(b['cluster_bootstrap_95'],1)])
        r=combined['repeat_consistency'][source];d=r['clustered_task_level_difference'];c=r['within_task_consistency']
        f=lambda st:f"{c[st]['identical_outcome_rate']*100:.2f}% (n={c[st]['tasks_with_three_known_repeats']})"
        t11.append([source.upper(),str(r['n_tasks_in_subset']),point(d['difference_bounds']),ci(d['cluster_bootstrap_outer95']),f('adaptive'),f('plan_reflect')])
    tables['9']=t9;tables['10']=t10;tables['11']=t11
    compare(tables,expected,'publication_tables',tol=0)
    for number,table in tables.items():
        with (out/f'Table_{int(number):02}.csv').open('w',encoding='utf-8-sig',newline='') as f:csv.writer(f).writerows(table)
    dump(out/'manuscript_tables.json',tables)
    return {'tables_matched':11,'table_cells_matched':sum(len(row) for t in tables.values() for row in t),'all_tables_exact_display_match':True}

def rescore(root,out):
    from tasks.dataset import Task
    from runner.scoring import score_final_answer
    from code.original_kernel import result
    tasks={}
    for tid,v in read(root/'data/tasks.json').items():
        tasks[tid]=Task.from_dict(dict(v,task_type='math',difficulty_anchor={'source':'archived_frozen_task'}))
    overlay=overlays(root); cache={};counts=Counter();differences=[];reason_diffs=0
    def score(r,source,archived,meta):
        nonlocal reason_diffs
        if r.get('error') or (r.get('status') and r['status']!='completed') or not str(r.get('final_answer') or '').strip():
            value=0;reason='zero_by_policy'
        else:
            ck=(r['task_id'],str(r['final_answer']))
            if ck not in cache:cache[ck]=score_final_answer(tasks[r['task_id']],r['final_answer'])
            s=cache[ck];value=None if s['needs_review'] else int(bool(s['success']));reason=s['score_reason']
        counts[source]+=1
        if value!=archived: differences.append(dict(meta,archived=archived,recomputed=value,reason=reason,answer_sha256=answer_sha(r)))
        return value,reason
    pop=read(root/'data/population.json')
    with (out/'rescored_original.jsonl').open('w',encoding='utf-8') as f:
        for spec in pop['cases']:
            for r in original_case(root,spec):
                meta={'case_id':spec['run_id'],'strategy':r['strategy'],'task_id':r['task_id'],'repeat_id':r['repeat_id']}
                v,reason=score(r,'original',result(r),meta);a=overlay.get((spec['run_id'],*key(r)))
                if a:assert a['answer_sha256']==answer_sha(r,True)
                record=dict(meta,frozen_recomputed=v,author_overlay_outcome=a['author_outcome'] if a else v,
                    answer_sha256=answer_sha(r),reason=reason)
                f.write(json.dumps(record,ensure_ascii=False)+'\n')
            print('rescored',spec['run_id'],counts['original'],flush=True)
    with (out/'rescored_followup.jsonl').open('w',encoding='utf-8') as f:
        for phase in ('main','repeats'):
            archived={r['run_id']:r for r in rows(root/f'data/followup/{phase}/scored.jsonl')}
            for r in rows(root/f'data/followup/{phase}/trajectories.jsonl'):
                v,reason=score(r,'followup',archived[r['run_id']]['value'],{'phase':phase,'run_id':r['run_id'],'task_id':r['task_id']})
                f.write(json.dumps({'run_id':r['run_id'],'phase':phase,'recomputed':v,'archived':archived[r['run_id']]['value'],'reason':reason,'answer_sha256':answer_sha(r)},ensure_ascii=False)+'\n')
    assert counts=={'original':51000,'followup':6400}
    dump(out/'rescore_differences.json',differences)
    report={'completed':True,'records_rescored':dict(counts),'distinct_task_answer_evaluations':len(cache),
        'label_differences':len(differences),'same_frozen_labels':not differences,'model_calls':0,
        'interpretation':'A replay in the recorded validation environment; any differences are disclosed, never applied to archived results.',
        'runtime':{p:importlib.metadata.version(p) for p in ('sympy','numpy','scipy')}}
    dump(out/'rescore_report.json',report);return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--mode',choices=['verify','analysis','rescore'],default='verify')
    args=parser.parse_args();root=args.input.resolve()
    out=(args.output or root.parent/(root.name+'_replay_'+args.mode)).resolve()
    assert out!=root and root not in out.parents,'Output must be outside the input package'
    assert not out.exists(),'Use a new output directory; existing results are preserved'
    out.mkdir(parents=True);sys.path.insert(0,str(root))
    started=time.time();report=verify(root,out)
    if args.mode=='analysis':
        orig,sens=original_analyses(root,out);a,b,arms=followup_analyses(root,out)
        report['analysis']=publication_tables(root,out,orig,sens,a,b,arms)
    elif args.mode=='rescore':report['rescore']=rescore(root,out)
    report['elapsed_seconds']=time.time()-started
    dump(out/'RUN_REPORT.json',report)
    print(json.dumps(report,ensure_ascii=True),flush=True)

if __name__=='__main__':main()
