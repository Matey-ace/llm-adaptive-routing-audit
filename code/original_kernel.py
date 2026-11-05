from collections import Counter,defaultdict
import math,random
from scipy.stats import beta

def key(r): return (r['strategy'], r['task_id'], r['repeat_id'])

def result(r):
    # Implement the protocol, not a call to the production outcome() helper.
    if r.get('error') or not str(r.get('final_answer', '')).strip(): return 0
    if r.get('needs_review') or r.get('success') is None: return None
    return 1 if r['success'] else 0

def cp(k,n,tail):
    return (0. if k==0 else float(beta.ppf(tail,k,n-k+1)),
            1. if k==n else float(beta.ppf(1-tail,k+1,n-k)))

def bounds(pairs):
    levels=defaultdict(list)
    for a,b in pairs:
        assert a['task_id']==b['task_id'] and a['repeat_id']==b['repeat_id'] and a['difficulty']==b['difficulty']
        levels[a['difficulty']].append((result(a),result(b)))
    assert len({a['task_id'] for a,b in pairs})==len(pairs)
    n=len(pairs);tail=(1-.95)/(4*len(levels));ci=[0.,0.];point=[0.,0.];strata={}
    for level,values in levels.items():
        st={};weight=len(values)/n
        for j,name in enumerate(('low_assignment','high_assignment')):
            assigned=[((j if x is None else x),(1-j if y is None else y)) for x,y in values]
            plus=sum(x>y for x,y in assigned);minus=sum(x<y for x,y in assigned);sn=len(values)
            lp,up=cp(plus,sn,tail);lm,um=cp(minus,sn,tail)
            st[name]={'plus':plus,'minus':minus,'n':sn,'difference':(plus-minus)/sn,'lower':lp-um,'upper':up-lm}
            point[j]+=weight*st[name]['difference']
            ci[j]+=weight*st[name]['lower' if j==0 else 'upper']
        strata[level]=st
    return {'n_tasks':n,'difference_bounds':point,'confidence_interval':ci,'noninferiority':ci[0]>-.03,'uncertain_pairs':sum(result(a) is None or result(b) is None for a,b in pairs),'strata':strata}

def pairs(rows,sa,sb):
    a={(r['task_id'],r['repeat_id']):r for r in rows if r['strategy']==sa}
    b={(r['task_id'],r['repeat_id']):r for r in rows if r['strategy']==sb}
    assert a.keys()==b.keys() and a
    return [(a[k],b[k]) for k in sorted(a)]

def bootstrap(pp,getter):
    # Same frozen PRNG and draw count; a separate implementation operating on
    # task-average differences, never resampling individual repeated responses.
    groups=defaultdict(list)
    for a,b in pp:
        x,y=getter(a),getter(b)
        if x is None or y is None or not math.isfinite(x) or not math.isfinite(y): return {'available':False}
        groups[(a['difficulty'],a['task_id'])].append((x,y))
    strata=defaultdict(list);xs=[];ys=[]
    for (level,task),values in groups.items():
        x=sum(v[0] for v in values)/len(values);y=sum(v[1] for v in values)/len(values)
        strata[level].append(x-y);xs.append(x);ys.append(y)
    rng=random.Random(20261014);n=len(groups);draws=[]
    for _ in range(2000):
        draws.append(sum(sum(rng.choice(v) for _ in range(len(v))) for v in strata.values())/n)
    draws.sort();ma=sum(xs)/n;mb=sum(ys)/n
    return {'available':True,'n_tasks':n,'n_paired_runs':len(pp),'mean_a':ma,'mean_b':mb,'difference_a_minus_b':ma-mb,'descriptive_percentile95':[draws[int(.025*1999)],draws[int(.975*1999)]]}

def summary(rows):
    out={}
    for st in sorted({r['strategy'] for r in rows}):
        v=[r for r in rows if r['strategy']==st];vals=[result(r) for r in v];n=len(v)
        attempts=[r['request_audit_summary']['recorded_request_attempts'] for r in v if r.get('request_audit_summary')]
        out[st]={'n':n,'correct':vals.count(1),'incorrect':vals.count(0),'unknown':vals.count(None),'accuracy_bounds':[vals.count(1)/n,(vals.count(1)+vals.count(None))/n],
                 'execution_errors':sum(bool(r.get('error')) for r in v),'empty_answers':sum(not str(r.get('final_answer') or '').strip() for r in v),
                 'mean_recorded_attempts':sum(attempts)/len(attempts) if attempts else None,
                 'stops':dict(Counter(str(r.get('stop_reason')) for r in v)),
                 'levels':dict(Counter(str(r.get('selected_level')) for r in v)),
                 'route_methods':dict(Counter(str(r.get('extra',{}).get('route_method')) for r in v)),
                 'known_usage_tokens':sum(u.get('total_tokens',0) for r in v for u in r.get('usage_records',[])),
                 'successful_usage_records':sum(len(r.get('usage_records',[])) for r in v),
                 'records_with_missing_billing':sum(bool(r.get('error') or r.get('failed_attempts')) or r.get('meter_summary',{}).get('total_cost_usd') is None for r in v)}
    return out
