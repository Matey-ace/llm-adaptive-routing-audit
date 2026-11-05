"""Offline baseline, human-audit and publication-table reconstruction."""
from pathlib import Path
import argparse, importlib.util, json, os, socket, subprocess, sys
sys.dont_write_bytecode=True
def deny(*a,**k):raise RuntimeError('Network connections are disabled in offline reproduction.')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny;socket.getaddrinfo=deny
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def load(name,p):
 spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,default=Path(__file__).resolve().parent);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--mode',choices=['verify','analysis','rescore'],default='verify');a=ap.parse_args()
 root=a.input.resolve();out=a.output.resolve();assert not out.exists() and out!=root and root not in out.parents
 out.mkdir(parents=True);sys.path.insert(0,str(root));env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
 subprocess.run([sys.executable,str(root/'code/legacy_reproduce.py'),'--input',str(root),'--output',str(out/'baseline'),'--mode',a.mode],check=True,env=env)
 subprocess.run([sys.executable,str(root/'code/audit_replay.py'),'--output',str(out/'audit')],check=True,env=env)
 support=load('audit_compare',root/'code/audit_support.py')
 for name in ('original_primary_statistics.json','original_repeat_statistics.json','original_case_summaries.json','followup_statistics.json','label_transitions.json'):
  support.compare(read(out/'audit'/name),read(root/'expected/human_audit'/name))
 ab=load('rating_agreement',root/'code/rating_agreement.py');materials=read(root/'data/human_review/paired_records_internal.json');agreement=[]
 for part,n,seed in [('primary_audit_300',300,20261020),('remaining_unknown',616,20261105),('followup_audit_300',300,20261021)]:
  rows=[r for r in materials if r['source_part']==part];assert len(rows)==n
  report=ab.stats(ab.matrix_of(rows));extra,_=ab.bootstrap(rows,seed);report.update(extra);report['part']=part;agreement.append(report)
  expected=next(p for p in read(root/'expected/human_agreement.json')['parts'] if p['part']==part)
  support.compare(report,{k:expected[k] for k in report})
 dump(out/'rating_agreement.json',{'parts':agreement})
 pub=load('audit_publication',root/'code/audit_publication.py');tables=pub.tables(agreement,read(out/'audit/original_primary_statistics.json'),read(out/'audit/followup_statistics.json'))
 support.compare(tables,read(root/'expected/audit_tables.json'))
 count=sum(len(row) for block in tables.values() for row in [block['header'],*block['rows']])
 dump(out/'audited_manuscript_tables.json',tables)
 result={'passed':True,'mode':a.mode,'baseline':read(out/'baseline/RUN_REPORT.json'),'human_audit_statistics_match':True,'pre_adjudication_agreement_rebuilt':True,'added_audit_table_cells_match':count,'new_model_calls':0,'network_blocked':True}
 dump(out/'RUN_REPORT.json',result);print(json.dumps({k:v for k,v in result.items() if k!='baseline'},indent=2))
if __name__=='__main__':main()
