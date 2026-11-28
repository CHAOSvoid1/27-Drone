import sys,time,re,csv,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'deps'))
from pymavlink import mavutil
root=Path(__file__).parent
c=mavutil.mavlink_connection('COM16',baud=115200,source_system=245,source_component=191)
def send(text):
 b=text.encode();c.mav.serial_control_send(10,6,0,0,len(b),list(b)+[0]*(70-len(b)))
try:
 h=c.wait_heartbeat(timeout=15)
 if h is None:raise RuntimeError('No heartbeat')
 if h.base_mode&128:raise RuntimeError('Armed; abort')
 send('\x03\n');time.sleep(.3)
 send('listener sensor_gps -n 3100\n')
 path=root/('gnss_clock_'+time.strftime('%Y%m%d_%H%M%S')+'.txt')
 start=time.monotonic();buf='';last=0
 with path.open('w',encoding='utf-8') as f:
  while time.monotonic()-start<330:
   m=c.recv_match(type='SERIAL_CONTROL',blocking=True,timeout=1)
   if m:
    text=bytes(m.data[:m.count]).decode(errors='replace');f.write(text);f.flush();buf+=text
   elapsed=time.monotonic()-start
   if elapsed-last>=30:
    last=elapsed
    print(json.dumps({'elapsed_s':round(elapsed),'utc_fields':len(re.findall(r'time_utc_usec:',buf)),'file':str(path),'preview':buf[:160] if elapsed<35 else ''}),flush=True)
   if elapsed>10 and 'nsh>' in buf and ('Usage:' in buf or 'not found' in buf):break
 print('DONE',str(path),flush=True)
finally:
 try:send('\x03')
 except Exception:pass
 c.close()
