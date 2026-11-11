"""Incremental, resumable history download. Each dataset is data/market/hist/<kind>/<ex>/<SYM>.csv (ts_ms,value[,value2]).
Every run continues from the last saved timestamp, so missed/killed runs never lose data (gaps self-heal
as long as the exchange still serves that range: funding = years; Binance OI = 30 days; Bybit OI = years)."""
import csv, os, sys, time
from common import get, f
ROOT="data/market/hist"; START=int(os.environ.get("HIST_START_MS", 1704067200000))  # 2024-01-01
BUDGET=float(os.environ.get("BUDGET_S", 2400)); T0=time.time()
def path(kind,ex,s): return f"{ROOT}/{kind}/{ex}/{s}.csv"
def last_ts(p):
    if not os.path.exists(p): return None
    with open(p,"rb") as fh:
        fh.seek(0,2); n=fh.tell(); fh.seek(max(0,n-300)); tail=fh.read().decode().strip().splitlines()
    try: return int(tail[-1].split(",")[0])
    except Exception: return None
def append(p,rows,hdr):
    os.makedirs(os.path.dirname(p),exist_ok=True); new=not os.path.exists(p)
    lt=last_ts(p) or -1; rows=sorted({r[0]:r for r in rows if r[0]>lt}.values())
    with open(p,"a",newline="") as fh:
        w=csv.writer(fh)
        if new: w.writerow(hdr)
        w.writerows(rows)
    return len(rows)
def out_of_time(): return time.time()-T0>BUDGET

# ---------- symbol lists ----------
def binance_syms():
    return sorted(s["symbol"] for s in get("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"]
                  if s["contractType"]=="PERPETUAL" and s["quoteAsset"]=="USDT" and s["status"]=="TRADING")
def bybit_syms():
    out=[]; cur=""
    while True:
        r=get("https://api.bybit.com/v5/market/instruments-info",{"category":"linear","limit":1000,"cursor":cur})["result"]
        out+=[x["symbol"] for x in r["list"] if x["quoteCoin"]=="USDT" and x["contractType"]=="LinearPerpetual" and x["status"]=="Trading"]
        cur=r.get("nextPageCursor")
        if not cur: return sorted(out)
def okx_syms():
    return sorted(x["instId"] for x in get("https://www.okx.com/api/v5/public/instruments",{"instType":"SWAP"})["data"]
                  if x["instId"].endswith("-USDT-SWAP") and x["state"]=="live")
def hl_syms():
    return [u["name"] for u in get("https://api.hyperliquid.xyz/info",body={"type":"meta"})["universe"] if not u.get("isDelisted")]

# ---------- funding ----------
def binance_funding(s):
    p=path("funding","binance",s[:-4]); st=(last_ts(p) or START-1)+1; n=0
    while not out_of_time():
        r=get("https://fapi.binance.com/fapi/v1/fundingRate",{"symbol":s,"startTime":st,"limit":1000}); time.sleep(0.65)  # 500 req/5min limit
        if not r: break
        n+=append(p,[(int(x["fundingTime"]),x["fundingRate"],x.get("markPrice","")) for x in r],["ts","rate","mark"])
        st=int(r[-1]["fundingTime"])+1
        if len(r)<1000: break
    return n
def bybit_funding(s):
    p=path("funding","bybit",s[:-4]); lt=last_ts(p) or START-1; end=int(time.time()*1000); rows=[]
    while not out_of_time():   # Bybit pages backwards from endTime
        r=get("https://api.bybit.com/v5/market/funding/history",{"category":"linear","symbol":s,"startTime":lt+1,"endTime":end,"limit":200})["result"]["list"]
        time.sleep(0.1)
        if not r: break
        rows+=[(int(x["fundingRateTimestamp"]),x["fundingRate"]) for x in r]
        end=int(r[-1]["fundingRateTimestamp"])-1
        if len(r)<200: break
    else: return 0  # out of time mid-symbol: write nothing so the backward paging restarts cleanly
    return append(p,rows,["ts","rate"])
def okx_funding(s):   # OKX serves ~3 months only
    p=path("funding","okx",s.split("-")[0]); lt=last_ts(p) or 0; after=""; rows=[]
    while not out_of_time():
        prm={"instId":s,"limit":100}
        if after: prm["after"]=after
        r=get("https://www.okx.com/api/v5/public/funding-rate-history",prm)["data"]; time.sleep(0.25)
        if not r: break
        rows+=[(int(x["fundingTime"]),x["realizedRate"] or x["fundingRate"]) for x in r]
        after=r[-1]["fundingTime"]
        if int(after)<=lt or len(r)<100: break
    else: return 0
    return append(p,rows,["ts","rate"])
def hl_funding(s):
    p=path("funding","hyperliquid",s); st=(last_ts(p) or max(START,1735689600000)-1)+1; n=0  # HL from 2025-01
    while not out_of_time():
        r=get("https://api.hyperliquid.xyz/info",body={"type":"fundingHistory","coin":s,"startTime":st}); time.sleep(1.1)
        if not r: break
        n+=append(p,[(int(x["time"]),x["fundingRate"],x.get("premium","")) for x in r],["ts","rate","premium"])
        st=int(r[-1]["time"])+1
        if len(r)<500: break
    return n
def gate_syms():
    return sorted(c["name"] for c in get("https://api.gateio.ws/api/v4/futures/usdt/contracts") if not c.get("in_delisting"))
def gate_funding(s):
    p=path("funding","gate",s[:-5]); st=(last_ts(p) or START-1000)//1000+1; n=0
    while not out_of_time():
        to=min(st+999*3600, int(time.time()))
        r=get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate",{"contract":s,"from":st,"to":to,"limit":1000}); time.sleep(0.12)
        if r: n+=append(p,[(int(x["t"])*1000,x["r"]) for x in r],["ts","rate"])
        st=to+1
        if to>=int(time.time())-60: break
    return n
# ---------- open interest (hourly) ----------
def binance_oi(s):   # Binance keeps 30 days only -> must run at least every few weeks
    p=path("oi","binance",s[:-4]); st=max((last_ts(p) or 0)+1, int(time.time()*1000)-29*86400000); n=0
    while not out_of_time():
        r=get("https://fapi.binance.com/futures/data/openInterestHist",{"symbol":s,"period":"1h","startTime":st,"limit":500}); time.sleep(0.15)
        if not r: break
        n+=append(p,[(int(x["timestamp"]),x["sumOpenInterest"],x["sumOpenInterestValue"]) for x in r],["ts","oi","oi_usd"])
        st=int(r[-1]["timestamp"])+1
        if len(r)<500: break
    return n

# reachable-from-GitHub first; Binance/Bybit are geo-blocked on most runners (Binance history comes from data.binance.vision instead)
JOBS=[("okx_funding",okx_syms,okx_funding),("hl_funding",hl_syms,hl_funding),("gate_funding",gate_syms,gate_funding),
      ("binance_oi",binance_syms,binance_oi),("bybit_funding",bybit_syms,bybit_funding),("binance_funding",binance_syms,binance_funding)]
if __name__=="__main__":
    only=sys.argv[1:]
    for name,lister,fn in JOBS:
        if only and name not in only: continue
        try: syms=lister()
        except Exception as e: print(f"{name}: symbol list failed {e}"); continue
        done=added=err=0
        for s in syms:
            if out_of_time(): break
            try: added+=fn(s); done+=1
            except Exception as e: err+=1; print(f"  {name} {s}: {str(e)[:100]}")
        print(f"{name:16s} symbols {done}/{len(syms)} new_rows {added} errors {err} t={time.time()-T0:.0f}s", flush=True)
        if out_of_time(): print("time budget reached; next run resumes"); break
