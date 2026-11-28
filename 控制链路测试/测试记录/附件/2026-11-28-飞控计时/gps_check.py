import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'deps'))
from pymavlink import mavutil
c=mavutil.mavlink_connection('COM16',baud=115200,source_system=245,source_component=191)
try:
 h=c.wait_heartbeat(timeout=10)
 if h is None:raise RuntimeError('No flight-controller heartbeat')
 print('HEARTBEAT',h.to_dict(),flush=True)
 for name in ['GPS_1_CONFIG','GPS_2_CONFIG']:
  c.mav.param_request_read_send(h.get_srcSystem(),h.get_srcComponent(),name.encode(),-1)
 for mid in [24,124,2]:
  c.mav.command_long_send(h.get_srcSystem(),h.get_srcComponent(),512,0,mid,0,0,0,0,0,0)
 end=time.monotonic()+20; count={}
 while time.monotonic()<end:
  m=c.recv_match(blocking=True,timeout=.5)
  if not m:continue
  kind=m.get_type()
  if kind in ['GPS_RAW_INT','GPS2_RAW','SYSTEM_TIME','PARAM_VALUE','STATUSTEXT','COMMAND_ACK']:
   if kind=='PARAM_VALUE' and not str(m.param_id).startswith('GPS_'):continue
   count[kind]=count.get(kind,0)+1
   if count[kind]<=5:print(json.dumps(m.to_dict()),flush=True)
 print('COUNTS',count,flush=True)
finally:c.close()
