"""Forward paper test: against the crowd at 4% directional changes (six majors).
Frozen rule: project doc prereg-against-crowd-dc-newcoins.md / prereg-round4. Do not edit parameters.
Uses only Binance public daily files (data.binance.vision), so it runs with a one-day delay.
Usage: python against_crowd_paper.py [outdir]  (cache dir: env PAPER_CACHE, default outdir/cache)
"""
import io, json, os, sys, zipfile, urllib.request, datetime as dt
import numpy as np, pandas as pd
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','DOGEUSDT','BNBUSDT']
TH=0.04; COST=0.0010; START=pd.Timestamp('2026-11-01'); WARM=pd.Timestamp('2026-08-01')
HERE=os.path.dirname(os.path.abspath(__file__)); EDGES={"BNBUSDT": [-0.207588, 0.131159], "BTCUSDT": [-0.199918, 0.195123], "DOGEUSDT": [-0.25104, 0.169816], "ETHUSDT": [-0.217278, 0.165312], "SOLUSDT": [-0.298977, 0.240396], "XRPUSDT": [-0.235508, 0.15898]}
OUT=sys.argv[1] if len(sys.argv)>1 else HERE; CACHE=os.environ.get('PAPER_CACHE',os.path.join(OUT,'cache')); os.makedirs(CACHE,exist_ok=True)
BASE='https://data.binance.vision/data/futures/um/daily'
def fetch(kind,s,d):
    name=f'{s}-1m-{d:%Y-%m-%d}.zip' if kind=='klines' else f'{s}-metrics-{d:%Y-%m-%d}.zip'
    p=os.path.join(CACHE,name)
    if not os.path.exists(p):
        url=f'{BASE}/klines/{s}/1m/{name}' if kind=='klines' else f'{BASE}/metrics/{s}/{name}'
        try:
            with urllib.request.urlopen(url,timeout=60) as r: b=r.read()
            open(p,'wb').write(b)
        except Exception: return None
    with zipfile.ZipFile(p) as z: raw=z.read(z.namelist()[0])
    if kind=='klines':
        d_=pd.read_csv(io.BytesIO(raw),header=None,low_memory=False)
        if str(d_.iloc[0,0]).startswith('open'): d_=d_.iloc[1:]
        return d_[[0,4]].astype(float).rename(columns={0:'t',4:'c'})
    d_=pd.read_csv(io.BytesIO(raw)); return pd.DataFrame({'t':pd.to_datetime(d_.create_time),'ls':d_.count_long_short_ratio.astype(float)})
def dc(x,th):
    ev=[]; mode=0; hi=lo=ext=x[0]
    for i in range(1,len(x)):
        v=x[i]
        if mode==0:
            hi=max(hi,v); lo=min(lo,v)
            if v<=hi-th: mode=-1; ext=v
            elif v>=lo+th: mode=1; ext=v
        elif mode==1:
            if v>ext: ext=v
            elif v<=ext-th: ev.append((i,-1)); mode=-1; ext=v
        else:
            if v<ext: ext=v
            elif v>=ext+th: ev.append((i,1)); mode=1; ext=v
    return ev
days=pd.date_range(WARM,pd.Timestamp(dt.datetime.now(dt.timezone.utc).date())-pd.Timedelta(days=1)); rows=[]; last={}
for s in SYMS:
    K=[];M=[]
    for d in days:
        k=fetch('klines',s,d); m=fetch('metrics',s,d)
        if k is None or m is None: break          # stop at the first day not yet published
        K.append(k); M.append(m); last[s]=d
    if not K: continue
    K=pd.concat(K).drop_duplicates('t').sort_values('t'); M=pd.concat(M).dropna().drop_duplicates('t').sort_values('t'); M=M[M.ls>0]
    t=K.t.values.astype('int64'); x=np.log(K.c.values)
    mt=M.t.values.astype('datetime64[ms]').astype('int64'); l=np.log(M.ls.values); dev=l-pd.Series(l).rolling(2016,min_periods=288).mean().values
    ev=dc(x,TH); lo_,hi_=EDGES[s]
    for j,(i,side) in enumerate(ev):
        te=t[i]-300000; k=np.searchsorted(mt,te,side='right')-1
        if k<0 or te-mt[k]>1800000 or np.isnan(dev[k]): continue
        crowd=-side*dev[k]
        dirn=1 if crowd>hi_ else (-1 if crowd<=lo_ else 0)
        if dirn==0 or pd.Timestamp(t[i],unit='ms')<START: continue
        r=dict(sym=s,entry_time=pd.Timestamp(t[i],unit='ms'),dc_side=side,crowd=round(float(crowd),4),action='follow' if dirn==1 else 'fade',position='long' if dirn*side>0 else 'short',entry=float(K.c.values[i]))
        if j+1<len(ev):
            i2=ev[j+1][0]; r.update(exit_time=pd.Timestamp(t[i2],unit='ms'),exit=float(K.c.values[i2]),net_bp=round((dirn*side*(x[i2]-x[i])-COST)*1e4,1))
        rows.append(r)
T=pd.DataFrame(rows); 
if len(T): T=T.sort_values('entry_time')
T.to_csv(os.path.join(OUT,'trades.csv'),index=False)
done=T.dropna(subset=['net_bp']) if len(T) and 'net_bp' in T else T.iloc[:0]
n=len(done); mean=done.net_bp.mean() if n else float('nan'); tstat=mean/done.net_bp.std()*np.sqrt(n) if n>2 else float('nan')
status='RUNNING'
if n>=300 and mean<-15: status='STOP (early-stop rule: mean below -15 bp after 300 trades)'
if n>=600 and mean>0 and tstat>2: status='CONFIRMED (>=600 trades, mean > 0, t > 2)'
summary=dict(data_through=str(min(last.values()).date()) if last else None,closed_trades=n,open_trades=int(len(T)-n),mean_net_bp=None if n==0 else round(float(mean),1),t_stat=None if n<=2 else round(float(tstat),2),win_rate=None if n==0 else round(float((done.net_bp>0).mean()*100),1),total_net_pct=None if n==0 else round(float(done.net_bp.sum()/100),2),status=status)
json.dump(summary,open(os.path.join(OUT,'summary.json'),'w'),indent=1); print(json.dumps(summary,indent=1))
