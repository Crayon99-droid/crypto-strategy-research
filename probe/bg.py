import json, urllib.request, urllib.parse, time
def get(u): return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'x'}),timeout=20).read())
syms=[c['symbol'] for c in get('https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES')['data']]
keys=['EUR','GBP','JPY','AUD','CHF','CAD','NZD','XAU','XAG','GOLD','SILVER','NDX','NAS','QQQ','SPY','SPX','US30','DJI','NVDA','TSLA','AAPL','MSTR','COIN','OIL','WTI','BRENT']
cand=sorted({s for s in syms if any(k in s for k in keys)})
print('candidates:',cand)
for s in cand+['BTCUSDT']:
    time.sleep(1.2)
    try:
        j=get('https://api.bitget.com/api/v2/mix/market/account-long-short?'+urllib.parse.urlencode({'symbol':s,'period':'5m'}))
        d=j.get('data') or []; print('OK ',s,len(d),d[-1] if d else '')
    except Exception as e: print('ERR',s,str(e)[:60])
