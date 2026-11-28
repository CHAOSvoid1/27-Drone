import csv,json,sys,statistics,math
from pathlib import Path
root=Path(__file__).parent
p=Path(sys.argv[1]) if len(sys.argv)>1 else sorted(root.glob('samples_*.csv'))[-1]
with p.open() as f: rows=[tuple(map(float,(r['pc_mid_s'],r['fc_s'],r['rtt_ms']))) for r in csv.DictReader(f)]
def fit(data):
 if len(data)<10:return None
 x0=data[0][0];y0=data[0][1]
 # Fit elapsed-time difference, avoiding cancellation in near-unit slopes.
 x=[r[0]-x0 for r in data]; d=[(r[1]-y0)-(r[0]-x0) for r in data]
 xm=statistics.mean(x);dm=statistics.mean(d);ss=sum((v-xm)**2 for v in x)
 b=sum((v-xm)*(z-dm) for v,z in zip(x,d))/ss
 residual=[z-dm-b*(v-xm) for v,z in zip(x,d)]
 return dict(samples=len(data),span_s=x[-1],ppm=b*1e6,drift_ms_per_100s=b*100000,residual_rms_ms=math.sqrt(statistics.mean(v*v for v in residual))*1000)
cut=statistics.median(r[2] for r in rows)
good=[r for r in rows if r[2]<=cut]
bins=[]
for i in range(int((rows[-1][0]-rows[0][0])//300)+1):
 data=[r for r in good if i*300<=r[0]-rows[0][0]<(i+1)*300]
 value=fit(data)
 if value:bins.append(dict(start_s=i*300,**value))
result=dict(file=str(p),total_samples=len(rows),duration_s=rows[-1][0]-rows[0][0],median_rtt_ms=cut,all_samples=fit(rows),low_rtt=fit(good),segments=bins)
(root/'analysis.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
