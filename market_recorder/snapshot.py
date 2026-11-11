"""Every run: one row per USDT perp per exchange. Funding, interval, mark, index, OI (USD), 24h volume (USD).
Snapshots are a bonus; anything critical (funding, OI) is also re-downloadable by history.py, so a missed run loses nothing important."""
import csv, gzip, os, sys, time, traceback, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from common import get, f
COLS=["ts","ex","sym","funding","interval_h","next_funding","mark","index","oi_usd","vol24_usd"]
now=int(time.time()*1000)

def binance():
    B="https://fapi.binance.com"
    pi=get(B+"/fapi/v1/premiumIndex"); tk={t["symbol"]:t for t in get(B+"/fapi/v1/ticker/24hr")}
    iv={x["symbol"]:x["fundingIntervalHours"] for x in get(B+"/fapi/v1/fundingInfo")}
    for p in pi:
        s=p["symbol"]
        if not s.endswith("USDT"): continue
        yield dict(sym=s[:-4],funding=f(p["lastFundingRate"]),interval_h=iv.get(s,8),next_funding=p["nextFundingTime"],
                   mark=f(p["markPrice"]),index=f(p["indexPrice"]),oi_usd=None,vol24_usd=f(tk.get(s,{}).get("quoteVolume")))
def bybit():
    B="https://api.bybit.com"
    iv={}; cur=""
    while True:
        r=get(B+"/v5/market/instruments-info",{"category":"linear","limit":1000,"cursor":cur})["result"]
        for x in r["list"]: iv[x["symbol"]]=(x.get("fundingInterval") or 480)/60
        cur=r.get("nextPageCursor");
        if not cur: break
    for t in get(B+"/v5/market/tickers",{"category":"linear"})["result"]["list"]:
        s=t["symbol"]
        if not s.endswith("USDT") or not t.get("fundingRate"): continue
        yield dict(sym=s[:-4],funding=f(t["fundingRate"]),interval_h=iv.get(s,8),next_funding=t.get("nextFundingTime"),
                   mark=f(t["markPrice"]),index=f(t["indexPrice"]),oi_usd=f(t.get("openInterestValue")),vol24_usd=f(t.get("turnover24h")))
def okx():
    B="https://www.okx.com/api/v5"
    tk={t["instId"]:t for t in get(B+"/market/tickers",{"instType":"SWAP"})["data"] if t["instId"].endswith("-USDT-SWAP")}
    oi={o["instId"]:f(o.get("oiUsd")) for o in get(B+"/public/open-interest",{"instType":"SWAP"})["data"]}
    mk={m["instId"]:f(m["markPx"]) for m in get(B+"/public/mark-price",{"instType":"SWAP"})["data"]}
    def fr(i):
        time.sleep(0.12); d=get(B+"/public/funding-rate",{"instId":i})["data"]; return d[0] if d else None
    with ThreadPoolExecutor(2) as ex: frs=list(ex.map(fr,list(tk)))
    for i,x in zip(tk,frs):
        if not x: continue
        t=tk[i]; ivh=(int(x["nextFundingTime"])-int(x["fundingTime"]))/3.6e6
        yield dict(sym=i.split("-")[0],funding=f(x["fundingRate"]),interval_h=ivh,next_funding=x["fundingTime"],
                   mark=mk.get(i),index=None,oi_usd=oi.get(i),vol24_usd=(f(t["volCcy24h"]) or 0)*(f(t["last"]) or 0))
def bitget():
    B="https://api.bitget.com/api/v2/mix/market"
    iv={}
    try:
        for x in get(B+"/current-fund-rate",{"productType":"USDT-FUTURES"})["data"]: iv[x["symbol"]]=f(x.get("fundingRateInterval"))
    except Exception: pass
    for t in get(B+"/tickers",{"productType":"USDT-FUTURES"})["data"]:
        s=t["symbol"]
        if not s.endswith("USDT"): continue
        m=f(t.get("markPrice"))
        yield dict(sym=s[:-4],funding=f(t.get("fundingRate")),interval_h=iv.get(s) or 8,next_funding=None,mark=m,index=f(t.get("indexPrice")),
                   oi_usd=(f(t.get("holdingAmount")) or 0)*(m or 0),vol24_usd=f(t.get("usdtVolume")) or f(t.get("quoteVolume")))
def gate():
    B="https://api.gateio.ws/api/v4/futures/usdt"
    tk={t["contract"]:t for t in get(B+"/tickers")}
    for c in get(B+"/contracts"):
        if c.get("in_delisting") or not c["name"].endswith("_USDT"): continue
        t=tk.get(c["name"],{}); m=f(c["mark_price"])
        oi=(f(t.get("total_size")) or 0)*(f(c.get("quanto_multiplier")) or 0)*(m or 0)
        yield dict(sym=c["name"][:-5],funding=f(c["funding_rate"]),interval_h=c["funding_interval"]/3600,next_funding=c.get("funding_next_apply",0)*1000,
                   mark=m,index=f(c.get("index_price")),oi_usd=oi,vol24_usd=f(t.get("volume_24h_quote")))
def mexc():
    B="https://contract.mexc.com/api/v1/contract"
    tk={t["symbol"]:t for t in get(B+"/ticker")["data"]}
    for x in get(B+"/funding_rate")["data"]:
        s=x["symbol"]
        if not s.endswith("_USDT"): continue
        t=tk.get(s,{})
        yield dict(sym=s[:-5],funding=f(x["fundingRate"]),interval_h=x.get("collectCycle",8),next_funding=x.get("nextSettleTime"),
                   mark=f(t.get("fairPrice")),index=f(t.get("indexPrice")),oi_usd=None,vol24_usd=f(t.get("amount24")))
def hyperliquid():
    meta,ctx=get("https://api.hyperliquid.xyz/info",body={"type":"metaAndAssetCtxs"})
    for u,c in zip(meta["universe"],ctx):
        if u.get("isDelisted"): continue
        m=f(c.get("markPx"))
        yield dict(sym=u["name"],funding=f(c["funding"]),interval_h=1,next_funding=None,mark=m,index=f(c.get("oraclePx")),
                   oi_usd=(f(c.get("openInterest")) or 0)*(m or 0),vol24_usd=f(c.get("dayNtlVlm")))

EX=dict(binance=binance,bybit=bybit,okx=okx,bitget=bitget,gate=gate,mexc=mexc,hyperliquid=hyperliquid)
def run(name):
    try: return name,[dict(ts=now,ex=name,**r) for r in EX[name]()],None
    except Exception: return name,[],traceback.format_exc(limit=2)
if __name__=="__main__":
    out=sys.argv[1] if len(sys.argv)>1 else "data/market/snap"
    d=dt.datetime.utcfromtimestamp(now/1000)
    path=f"{out}/{d:%Y-%m-%d}/{d:%H%M}.csv.gz"; os.makedirs(os.path.dirname(path),exist_ok=True)
    with ThreadPoolExecutor(7) as ex: res=list(ex.map(run,EX))
    rows=[r for _,rs,_ in res for r in rs]
    with gzip.open(path,"wt",newline="") as fh:
        w=csv.DictWriter(fh,COLS); w.writeheader(); w.writerows(rows)
    log=f"{out}/../health.csv"; new=not os.path.exists(log)
    with open(log,"a") as fh:
        if new: fh.write("ts_utc,ex,rows,error\n")
        for n,rs,err in res: fh.write(f"{d:%Y-%m-%dT%H:%M},{n},{len(rs)},{'' if not err else err.strip().splitlines()[-1][:120].replace(',',';')}\n")
    for n,rs,err in res: print(f"{n:12s} {len(rs):5d} rows", "ERROR "+err.strip().splitlines()[-1] if err else "")
    print("wrote",path,len(rows))
    if sum(1 for _,rs,_ in res if rs)<3: sys.exit("fewer than 3 exchanges returned data")
