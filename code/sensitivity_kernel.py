from collections import Counter,defaultdict
from functools import lru_cache
from statistics import NormalDist
import numpy as np
from scipy.stats import beta,binomtest

def wilson(k,n):
    p=np.asarray(k)/n;z=NormalDist().inv_cdf(.975);den=1+z*z/n
    center=(p+z*z/(2*n))/den
    radius=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return center-radius,center+radius

def newcombe10(e,f,g,h):
    e,f,g,h=[np.asarray(v,dtype=float) for v in [e,f,g,h]]
    n=e+f+g+h
    if np.any(n<=0):raise ValueError('nonempty table required')
    pa,pb=(e+f)/n,(e+g)/n
    la,ua=wilson(e+f,n);lb,ub=wilson(e+g,n)
    numerator=e*h-f*g
    numerator=np.where(numerator>0,np.maximum(numerator-n/2,0),numerator)
    denominator=np.sqrt((e+f)*(g+h)*(e+g)*(f+h))
    corr=np.divide(numerator,denominator,out=np.zeros_like(numerator),where=denominator>0)
    lo=pa-pb-np.sqrt(np.maximum(0,(pa-la)**2+(ub-pb)**2-2*corr*(pa-la)*(ub-pb)))
    hi=pa-pb+np.sqrt(np.maximum(0,(ua-pa)**2+(pb-lb)**2-2*corr*(ua-pa)*(pb-lb)))
    return lo,hi

@lru_cache(maxsize=64)
def cp_lookup(n,H):
    tail=.05/(4*H);k=np.arange(n+1)
    low=np.zeros(n+1);high=np.ones(n+1)
    low[1:]=beta.ppf(tail,k[1:],n-k[1:]+1)
    high[:-1]=beta.ppf(1-tail,k[:-1]+1,n-k[:-1])
    return low,high

def outcome(r):
    if r['error'] or not r['answer'].strip():return 0
    if r['needs_review'] or r['success'] is None:return None
    return int(bool(r['success']))

def counts(pairs,policy):
    table=Counter()
    for a,b in pairs:
        x,y=outcome(a),outcome(b)
        if policy=='known' and (x is None or y is None):continue
        if policy=='low':x,y=0 if x is None else x,1 if y is None else y
        else:x,y=0 if x is None else x,0 if y is None else y
        table[(x,y)]+=1
    return [table[(1,1)],table[(1,0)],table[(0,1)],table[(0,0)]]

def cp_envelope(pairs):
    by=defaultdict(list)
    for a,b in pairs:by[a['difficulty']].append((a,b))
    n=len(pairs)
    if n==0:return None
    low=high=pl=pu=0.
    for sub in by.values():
        ns=len(sub);lower,upper=cp_lookup(ns,len(by));poslo=neglo=poshi=neghi=0
        for a,b in sub:
            x,y=outcome(a),outcome(b)
            xl,yl=0 if x is None else x,1 if y is None else y
            xh,yh=1 if x is None else x,0 if y is None else y
            poslo+=xl>yl;neglo+=xl<yl;poshi+=xh>yh;neghi+=xh<yh
        low+=ns/n*(lower[poslo]-upper[neglo]);high+=ns/n*(upper[poshi]-lower[neghi])
        pl+=(poslo-neglo)/n;pu+=(poshi-neghi)/n
    return {'n':n,'difference_bounds':[pl,pu],'confidence_interval':[float(low),float(high)],
            'lower_gt_minus_003':bool(low>-.03),'unknown_pairs':sum(outcome(a) is None or outcome(b) is None for a,b in pairs),
            'strata':{k:len(v) for k,v in by.items()}}

def score_summary(pairs,policy):
    e,f,g,h=counts(pairs,policy);n=e+f+g+h
    if not n:return {'n':0}
    lo,hi=newcombe10(e,f,g,h)
    return {'n':n,'table':{'both_correct':e,'a_only':f,'b_only':g,'neither_correct':h},
            'difference':(f-g)/n,'newcombe10_95':[float(lo),float(hi)],
            'post_test_lower_gt_minus_003':bool(lo>-.03),
            'mcnemar_exact_zero_difference_p':float(binomtest(f,f+g,.5).pvalue) if f+g else 1.}

def validate_method():
    # Original article Table III, method 10; e,f,g,h notation as printed.
    examples=[([36,12,2,0],[.0569,.3404]),([20,12,2,16],[.0562,.3292]),
              ([35,14,0,1],[.1461,.4175]),([2,98,0,0],[.9178,.9945]),
              ([27,0,0,27],[-.0351,.0351]),([54,0,0,0],[-.0664,.0664])]
    checks=[]
    for table,expected in examples:
        actual=[float(x) for x in newcombe10(*table)]
        assert max(abs(a-b) for a,b in zip(actual,expected))<.000051,(table,actual,expected)
        flipped=newcombe10(table[0],table[2],table[1],table[3])
        assert np.allclose(flipped,[-actual[1],-actual[0]],atol=1e-12)
        checks.append({'counts':table,'published_4dp':expected,'computed':actual})
    # Check boundaries, negative association, and positive/negative differences.
    for t in [[0,0,0,20],[20,0,0,0],[0,20,0,0],[0,0,20,0],[0,4,5,0]]:
        lo,hi=newcombe10(*t);assert -1-1e-12<=lo<=hi<=1+1e-12
    return checks
