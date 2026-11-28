import sys, time, csv, json, statistics
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'deps'))
from pymavlink import mavutil
from serial.tools import list_ports

if len(sys.argv) < 2:
    print([(p.device,p.description,p.hwid) for p in list_ports.comports()], flush=True)
    sys.exit()
port=sys.argv[1]
duration=int(sys.argv[2]) if len(sys.argv)>2 else 1800
c=mavutil.mavlink_connection(port, baud=115200, source_system=245, source_component=191)
h=c.wait_heartbeat(timeout=15)
if h is None: raise RuntimeError('No heartbeat; abort')
if h.base_mode & 128: raise RuntimeError('Vehicle armed; abort')
print('Heartbeat',h.get_srcSystem(),h.autopilot,'port',port,flush=True)
out=Path(__file__).parent / ('samples_'+time.strftime('%Y%m%d_%H%M%S')+'.csv')
start=time.perf_counter_ns(); pending={}; samples=[]; next_send=0; report=0
with out.open('w',newline='') as f:
 w=csv.writer(f); w.writerow(['pc_mid_s','fc_s','rtt_ms'])
 while (time.perf_counter_ns()-start)/1e9 < duration:
  now=time.perf_counter_ns()
  if now>=next_send:
   c.mav.timesync_send(0,now); pending[now]=now; next_send=now+200000000
   pending={k:v for k,v in pending.items() if now-v<2000000000}
  m=c.recv_match(blocking=True,timeout=.02)
  received=time.perf_counter_ns()
  if m and m.get_type()=='TIMESYNC' and m.tc1>0 and m.ts1 in pending:
   sent=pending.pop(m.ts1); row=((sent+received)/2e9,m.tc1/1e9,(received-sent)/1e6)
   samples.append(row); w.writerow(row)
  elapsed=(received-start)/1e9
  if elapsed>=report:
   f.flush(); report=elapsed+30
   result={'elapsed_s':round(elapsed,1),'samples':len(samples),'file':str(out)}
   if len(samples)>30:
    cutoff=sorted(r[2] for r in samples)[len(samples)//2]
    good=[r for r in samples if r[2]<=cutoff]
    x0=good[0][0]; y0=good[0][1]
    xs=[r[0]-x0 for r in good]; ys=[r[1]-y0 for r in good]
    xm=statistics.mean(xs); ym=statistics.mean(ys)
    slope=sum((x-xm)*(y-ym) for x,y in zip(xs,ys))/sum((x-xm)**2 for x in xs)
    result.update(ppm=round((slope-1)*1e6,3),median_rtt_ms=round(cutoff,3),span_s=round(xs[-1],1))
   print(json.dumps(result),flush=True)
   (Path(__file__).parent/'latest.json').write_text(json.dumps(result,indent=2))
c.close()
print('DONE',flush=True)
