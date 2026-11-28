import re,json,statistics,math
from pathlib import Path
root=Path(__file__).parent
p=sorted(root.glob('gnss_clock_*.txt'))[-1]
rows=[]
for block in p.read_text(encoding='utf-8').split('TOPIC: sensor_gps instance 0'):
 def field(name):
  m=re.search(r'^\s*'+name+r':\s*(-?\d+)',block,re.M)
  return int(m.group(1)) if m else None
 t,u,f,s,rel=[field(k) for k in ['timestamp','time_utc_usec','fix_type','satellites_used','timestamp_time_relative']]
 if None not in (t,u,f,s,rel) and u>0 and f>=3:rows.append((t+rel,u,f,s))
def fit(data):
 if len(data)<5:return None
 t0,u0=data[0][:2];x=[(r[1]-u0)/1e6 for r in data];d=[((r[0]-t0)-(r[1]-u0))/1e6 for r in data]
 xm=statistics.mean(x);dm=statistics.mean(d);ss=sum((v-xm)**2 for v in x)
 if ss==0:return None
 b=sum((v-xm)*(z-dm) for v,z in zip(x,d))/ss
 residual=[z-dm-b*(v-xm) for v,z in zip(x,d)]
 return dict(samples=len(data),gnss_span_s=x[-1],ppm=b*1e6,drift_ms_per_100s=b*1e5,residual_rms_ms=math.sqrt(statistics.mean(v*v for v in residual))*1e3)
result=dict(file=str(p),fit=fit(rows),satellites_range=[min(r[3] for r in rows),max(r[3] for r in rows)] if rows else [],segments=[])
if rows:
 for i in range(int((rows[-1][1]-rows[0][1])/60e6)+1):
  f=fit([r for r in rows if i*60e6<=r[1]-rows[0][1]<(i+1)*60e6])
  if f:result['segments'].append(dict(start_s=i*60,**f))
(root/'gnss_analysis.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
