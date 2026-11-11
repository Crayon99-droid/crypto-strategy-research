"""Hourly Deribit options snapshot (Helios Macro). Stdlib only.
Saves per run, for BTC and ETH:
  data/deribit/YYYY/MM/DD/HH_<CUR>_book.csv.gz    every option: OI, mark IV, prices, underlying
  data/deribit/YYYY/MM/DD/HH_<CUR>_trades.csv.gz  option trades since the previous run (backfilled up to 48h) (direction, size, IV, block flag)
  data/deribit/summary.csv                        one row per currency per run: spot, OI, call/put gamma exposure
Gamma is computed with Black-Scholes from Deribit's mark IV (r=0).
call_gex / put_gex = sum(OI * gamma) * S^2 * 0.01  (USD change in delta per 1% move), kept separate so the
dealer-sign assumption can be tested later instead of baked in."""
import json, urllib.request, gzip, csv, io, os, math, time, datetime as dt
API='https://www.deribit.com/api/v2/public/'
def get(m,**p):
    q='&'.join(f'{k}={v}' for k,v in p.items())
    for t in range(5):
        try:
            with urllib.request.urlopen(f'{API}{m}?{q}',timeout=30) as r: return json.load(r)['result']
        except Exception as e:
            if t==4: raise
            time.sleep(3*(t+1))
def ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def gamma(S,K,T,iv):
    if T<=0 or iv<=0: return 0.0
    d1=(math.log(S/K)+0.5*iv*iv*T)/(iv*math.sqrt(T))
    return math.exp(-0.5*d1*d1)/math.sqrt(2*math.pi)/(S*iv*math.sqrt(T))
def write_gz(path,rows,fields):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    buf=io.StringIO(); w=csv.DictWriter(buf,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    with gzip.open(path,'wt') as f: f.write(buf.getvalue())
now=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)
base=f"data/deribit/{now:%Y/%m/%d}/{now:%H%M}"
STATE='data/deribit/state.json'
state=json.load(open(STATE)) if os.path.exists(STATE) else {}
summ='data/deribit/summary.csv'; new=not os.path.exists(summ)
os.makedirs('data/deribit',exist_ok=True)
with open(summ,'a',newline='') as sf:
    sw=csv.writer(sf)
    if new: sw.writerow(['ts','cur','spot','n_options','call_oi','put_oi','call_gex_usd','put_gex_usd','net_gex_usd_callpos','top_oi_strike','trades_since_last','block_trades_since_last','buy_call_vol','sell_call_vol','buy_put_vol','sell_put_vol'])
    for cur in ('BTC','ETH'):
        book=get('get_book_summary_by_currency',currency=cur,kind='option')
        rows=[];cg=pg=coi=poi=0.0; oi_by_k={}
        for b in book:
            _,exp,k,typ=b['instrument_name'].split('-'); k=float(k)
            ex=dt.datetime.strptime(exp,'%d%b%y').replace(hour=8,tzinfo=dt.timezone.utc)
            T=(ex-now).total_seconds()/(365*86400); S=b.get('underlying_price') or 0; iv=(b.get('mark_iv') or 0)/100; oi=b.get('open_interest') or 0
            g=gamma(S,k,T,iv) if S else 0; gex=oi*g*S*S*0.01
            if typ=='C': cg+=gex; coi+=oi
            else: pg+=gex; poi+=oi
            oi_by_k[k]=oi_by_k.get(k,0)+oi
            rows.append(dict(ts=now.isoformat(),instrument=b['instrument_name'],expiry=ex.date(),strike=k,type=typ,
              open_interest=oi,mark_iv=b.get('mark_iv'),mark_price=b.get('mark_price'),bid=b.get('bid_price'),ask=b.get('ask_price'),
              underlying=S,volume_24h=b.get('volume'),gamma=round(g,10)))
        f=list(rows[0].keys()) if rows else ['ts']
        write_gz(f'{base}_{cur}_book.csv.gz',rows,f)
        end=int(now.timestamp()*1000); start=max(state.get(cur,end-3600*1000)+1, end-48*3600*1000)  # backfill since last run (max 48h)
        tr=[];cont=True;e=end
        while cont:
            r=get('get_last_trades_by_currency_and_time',currency=cur,kind='option',start_timestamp=start,end_timestamp=e,count=1000,sorting='desc')
            tr+=r['trades']; cont=r.get('has_more') and r['trades']
            if cont: e=r['trades'][-1]['timestamp']-1
        vol={('buy','C'):0,('sell','C'):0,('buy','P'):0,('sell','P'):0}
        for t in tr:
            vol[(t['direction'],t['instrument_name'][-1])]+=t['amount']
        tf=['timestamp','instrument_name','direction','amount','price','iv','index_price','mark_price','block_trade_id','liquidation','trade_id']
        write_gz(f'{base}_{cur}_trades.csv.gz',tr,tf)
        spot=rows[0]['underlying'] if rows else ''
        topk=max(oi_by_k,key=oi_by_k.get) if oi_by_k else ''
        sw.writerow([now.isoformat(),cur,spot,len(rows),round(coi,2),round(poi,2),round(cg),round(pg),round(cg-pg),topk,
                     len(tr),sum(1 for t in tr if t.get('block_trade_id')),*(round(vol[k],2) for k in [('buy','C'),('sell','C'),('buy','P'),('sell','P')])])
        if tr: state[cur]=max(t['timestamp'] for t in tr)
        else: state[cur]=end
        print(cur,len(rows),'options',len(tr),'trades since',dt.datetime.fromtimestamp(start/1000,dt.timezone.utc))
json.dump(state,open(STATE,'w'))
