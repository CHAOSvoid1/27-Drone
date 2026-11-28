from pathlib import Path
import csv,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
root=Path(__file__).parent
a=json.loads((root/'analysis.json').read_text())
with Path(a['file']).open() as f: rows=[tuple(map(float,(r['pc_mid_s'],r['fc_s'],r['rtt_ms']))) for r in csv.DictReader(f)]
x0,y0,_=rows[0];x=[r[0]-x0 for r in rows];y=[((r[1]-y0)-(r[0]-x0))*1000 for r in rows]
fig,axes=plt.subplots(2,1,figsize=(10,6),sharex=True,gridspec_kw={'height_ratios':[2,1]})
axes[0].scatter(x,y,s=4,alpha=.4,label='TIMESYNC samples')
axes[0].set_ylabel('FC elapsed - PC elapsed (ms)')
axes[0].set_title('PX4 USB clock test: relative drift %.1f ppm'%a['low_rtt']['ppm'])
axes[0].legend();axes[1].scatter(x,[r[2] for r in rows],s=4,alpha=.4)
axes[1].set_ylabel('Round trip (ms)');axes[1].set_xlabel('PC elapsed time (s)')
for ax in axes:ax.grid(alpha=.25)
fig.tight_layout();fig.savefig(root/'clock_drift.png',dpi=160)
