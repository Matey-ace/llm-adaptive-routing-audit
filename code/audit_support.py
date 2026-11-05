from datetime import date
import math

def compare(actual, expected):
    if isinstance(expected, dict):
        for key in expected:
            assert key in actual, key
            compare(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected):
            compare(a, b)
    elif isinstance(expected, bool) or expected is None:
        assert actual is expected, (actual, expected)
    elif isinstance(expected, int):
        assert actual == expected, (actual, expected)
    elif isinstance(expected, float):
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-10), (actual, expected)
    else:
        assert actual == expected

def validate_decisions(records, expected, as_of):
    assert len(records) == len(expected), 'A complete set of 87 reviewed materials is required.'
    by = {r['review_id']: r for r in records}
    assert len(by) == len(records) and set(by) == set(expected), 'Duplicate, missing or extra material ID.'
    for rid, r in by.items():
        e = expected[rid]
        assert r['task_id'] == e['task_id'] and r['answer_sha256'] == e['answer_sha256'], 'Answer identity mismatch.'
        if 'candidate_answer' in r:
            assert r['candidate_answer'] == e['candidate_answer'], 'Answer text changed.'
        if 'problem' in r:
            assert r['problem'] == e['problem'], 'Problem text changed.'
        assert r.get('adjudication_decision') in ('correct', 'incorrect', 'unresolved'), 'Missing or invalid actual decision.'
        for key in ['adjudicator', 'adjudication_rationale']:
            assert isinstance(r.get(key), str) and r[key].strip(), 'Missing actual author identity or rationale.'
        assert isinstance(r.get('adjudicated_at'), str), 'Missing actual review date.'
        when = date.fromisoformat(r['adjudicated_at'])
        assert when.isoformat() == r['adjudicated_at'] and when <= as_of, 'Invalid or future review date.'
    return by
