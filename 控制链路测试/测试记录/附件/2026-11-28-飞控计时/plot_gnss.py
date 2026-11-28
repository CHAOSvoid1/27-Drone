import runpy
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
root=Path(__file__).parent
a=runpy.run_path(str(root/'analyze_gnss.py'));rows=a['rows'];result=a['result']
t0,u0=rows[0][:2]
x=[(r[1]-u0)/1e6 for r in rows];d=[((r[0]-t0)-(r[1]-u0))/1000 for r in rows]
fig,ax=plt.subplots(figsize=(9,4.5));ax.scatter(x,d,s=4,alpha=.5)
ax.set(xlabel='GNSS UTC elapsed time (s)',ylabel='FC elapsed - GNSS elapsed (ms)',title='PX4 vs GNSS serial timestamp: %.0f ppm'%result['fit']['ppm'])
ax.grid(alpha=.25);fig.tight_layout();fig.savefig(root/'gnss_clock_drift.png',dpi=160)
