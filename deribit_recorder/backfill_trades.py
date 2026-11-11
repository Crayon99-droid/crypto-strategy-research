"""One-off / resumable: download all BTC+ETH option trades from START to now into daily files
data/deribit/trades_daily/<CUR>/YYYY-MM-DD.csv.gz. Complete days only are skipped on rerun."""
import json, urllib.request, urllib.parse, gzip, csv, os, sys, time, datetime as dt
H='https://history.deribit.com/api/v2/public/'
def get(m,**p):
    for i in range(5):
        try:
            with urllib.request.urlopen(H+m+'?'+urllib.parse.urlencode(p),timeout=30) as r: return json.load(r)['result']
        except Exception as e:
            if i==4: raise
            time.sleep(3*(i+1))
F=['timestamp','instrument_name','direction','amount','price','iv','index_price','mark_price','block_trade_id','liquidation','trade_id']
start=dt.date.fromisoformat(sys.argv[1] if len(sys.argv)>1 else '2026-10-01'); today=dt.datetime.now(dt.timezone.utc).date()
T0=time.time()
for cur in ('BTC','ETH'):
    d=start
    while d<today:
        p=f'data/deribit/trades_daily/{cur}/{d}.csv.gz'
        if not os.path.exists(p):
            s=int(dt.datetime(d.year,d.month,d.day,tzinfo=dt.timezone.utc).timestamp()*1000); e=s+86400000-1; tr=[]
            while True:
                r=get('get_last_trades_by_currency_and_time',currency=cur,kind='option',start_timestamp=s,end_timestamp=e,count=1000,sorting='asc')
                tr+=r['trades']
                if not r.get('has_more') or not r['trades']: break
                s=r['trades'][-1]['timestamp']+1
            seen=set(); tr=[t for t in tr if not (t['trade_id'] in seen or seen.add(t['trade_id']))]
            os.makedirs(os.path.dirname(p),exist_ok=True)
            with gzip.open(p,'wt',newline='') as fh:
                w=csv.DictWriter(fh,F,extrasaction='ignore'); w.writeheader(); w.writerows(tr)
            print(cur,d,len(tr),'trades',f'{time.time()-T0:.0f}s',flush=True)
        d+=dt.timedelta(days=1)
