import urllib.request, urllib.error, time
B="https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=BTC_USDT"
now=int(time.time())
for q in ["&limit=5", f"&from={now-86400*3}&to={now}", f"&from={now-86400*3}&to={now}&limit=1000", f"&from={now-86400*40}&to={now}", f"&from=1704067200&to={1704067200+999*3600}", f"&from=1704067200&limit=1000", f"&to=1706659200&limit=1000"]:
    try:
        r=urllib.request.urlopen(B+q,timeout=15).read(); print("OK ",q,len(r),r[:120])
    except urllib.error.HTTPError as e: print("ERR",q,e.code,e.read()[:200])
