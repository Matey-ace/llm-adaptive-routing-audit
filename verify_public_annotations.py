"""Verify the public ledger without benchmark texts, model outputs or dependencies."""
from pathlib import Path
import hashlib,json
from collections import Counter
ROOT=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def main():
 for e in read(ROOT/'PUBLIC_MANIFEST.json')['files']:
  p=ROOT/e['path'];assert p.stat().st_size==e['bytes']
  assert hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256'],e['path']
 rows=read(ROOT/'annotations/paired_labels_1216.json');decisions=read(ROOT/'annotations/author_adjudications_87.json')
 assert len(rows)==1216 and len({r['review_id'] for r in rows})==1216
 assert len(decisions)==87 and len({r['review_id'] for r in decisions})==87
 labels={'correct','incorrect','unresolved'}
 for r in rows:
  assert len(r['answer_sha256'])==64
  assert all(r[w]['semantic_decision'] in labels for w in ['A','B'])
 expected=read(ROOT/'expected/human_agreement.json')['parts'];strata=[]
 for p in expected:
  rr=[r for r in rows if r['source_part']==p['part']];n=len(rr)
  agree=sum(r['A']['semantic_decision']==r['B']['semantic_decision'] for r in rr)
  a=Counter(r['A']['semantic_decision'] for r in rr);b=Counter(r['B']['semantic_decision'] for r in rr)
  pe=sum(a[k]*b[k] for k in labels)/n**2;kappa=(agree/n-pe)/(1-pe)
  assert n==p['n'] and agree==p['agree'] and abs(kappa-p['kappa'])<1e-10
  strata.append({'part':p['part'],'n':n,'agree':agree,'kappa':kappa})
 counts=Counter(r['adjudication_decision'] for r in decisions)
 assert counts=={'correct':63,'incorrect':21,'unresolved':3}
 print(json.dumps({'passed':True,'paired_materials':len(rows),'adjudications':dict(counts),'strata':strata},indent=2))
if __name__=='__main__':main()
