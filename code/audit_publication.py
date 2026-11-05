def ci(v):return '['+', '.join(f'{x*100:+.4f}' for x in v)+']'
def bound(v):return f'{v[0]*100:+.4f}' if abs(v[0]-v[1])<1e-10 else ci(v)
def tables(agreement,original,follow):
 data12=[]
 for i,p in enumerate(agreement):data12.append([['Original audit','Original unknown','Follow-up audit'][i],str(p['n']),f'{p["agree"]}/{p["n"]}',f'{p["kappa"]:.6f}'])
 data13=[];names={'formal_gsm8k_primary_20261014':'DS GSM8K','formal_math_primary_20261014':'DS MATH','formal_gsm8k_qwen_20261014':'Qwen GSM8K','formal_math_qwen_20261014':'Qwen MATH'}
 for case,versions in original.items():
  for version,label in [('audit_original_gold','Original gold'),('audit_gold_sensitivity','Gold sensitivity')]:
   q=versions[version]['primary_stratified_cp'];data13.append([names[case],label,bound(q['difference_bounds']),ci(q['confidence_interval']),str(q['uncertain_pairs'])])
 data14=[]
 for src in ['gsm8k','math']:
  for version,label in [('baseline','Automatic'),('audit_original_gold','Original gold'),('audit_gold_sensitivity','Gold sensitivity')]:
   q=follow[version]['sources'][src]['primary_stratified_cp'];cc=follow[version]['label_counts_by_phase_source_strategy']
   def counts(st):return ' / '.join(str(cc[f'main/{src}/{st}'][k]) for k in ['correct','incorrect','unknown'])
   data14.append([src.upper(),label,counts('adaptive'),counts('plan_reflect'),ci(q['confidence_interval'])])
 return {'12':{'header':['Material layer','N','Agreement','Cohen κ'],'rows':data12},'13':{'header':['Comparison','Audit version','Difference pp','CP 95% pp','U'],'rows':data13},'14':{'header':['Dataset','Score version','Adaptive C / I / U','Reference C / I / U','CP 95% pp'],'rows':data14}}
