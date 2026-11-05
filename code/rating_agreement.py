from collections import defaultdict
import random
LABELS=['correct','incorrect','unresolved']

def stats(matrix):
 n=sum(map(sum,matrix));agree=sum(matrix[i][i] for i in range(3))
 if not n:return {'n':0,'agree':0,'disagree':0,'agreement':None,'kappa':None,'matrix':matrix}
 marginal=sum(sum(matrix[i])*sum(row[i] for row in matrix) for i in range(3))
 denominator=n*n-marginal
 return {'n':n,'agree':agree,'disagree':n-agree,'agreement':agree/n,
         'kappa':(n*agree-marginal)/denominator if denominator else None,'matrix':matrix}

def matrix_of(rows):
 m=[[0]*3 for _ in range(3)]
 for r in rows:m[LABELS.index(r['A']['semantic_decision'])][LABELS.index(r['B']['semantic_decision'])]+=1
 return m

def interval(values):
 values=sorted(values)
 return [values[int(.025*(len(values)-1))],values[int(.975*(len(values)-1))]] if values else None

def bootstrap(rows,seed):
 by=defaultdict(list)
 for r in rows:by[r['task_id']].append(r)
 ids=sorted(by);ms=[matrix_of(by[k]) for k in ids];rng=random.Random(seed);samples=[]
 # Verify the optimized matrix sum against direct flattening for fixed draws.
 direct=random.Random(seed)
 for draw in range(2000):
  picked=rng.choices(range(len(ids)),k=len(ids));m=[[sum(ms[k][i][j] for k in picked) for j in range(3)] for i in range(3)]
  if draw<30:
   chosen=direct.choices(range(len(ids)),k=len(ids))
   assert picked==chosen and matrix_of([r for k in chosen for r in by[ids[k]]])==m
  value=stats(m);samples.append({'agreement':value['agreement'],'kappa':value['kappa']})
 ks=[r['kappa'] for r in samples if r['kappa'] is not None]
 return {'task_clusters':len(ids),'bootstrap_draws':2000,'seed':seed,'task_order':'sorted exact task IDs',
         'agreement_task_cluster_bootstrap95':interval([r['agreement'] for r in samples]),
         'kappa_task_cluster_bootstrap95':interval(ks),'defined_kappa_draws':len(ks),
         'interval_role':'Descriptive task-cluster percentile interval; not a population error rate or a noninferiority test.'},samples
