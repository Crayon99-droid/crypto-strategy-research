import urllib.request, json, time
U = {
 "binance_fapi_klines": "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=2",
 "binance_fapi_ticker": "https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT",
 "binance_fapi_funding": "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT",
 "binance_fapi_oi_hist": "https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=2",
 "binance_spot": "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
 "binance_data_api": "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT",
 "binance_vision_files": "https://data.binance.vision/?prefix=data/futures/um/daily/klines/BTCUSDT/1h/",
 "bybit_v5": "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT",
 "okx": "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP",
 "bitget": "https://api.bitget.com/api/v2/mix/market/ticker?symbol=BTCUSDT&productType=USDT-FUTURES",
 "mexc": "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT",
 "gate": "https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=BTC_USDT",
 "hyperliquid": None,
}
for k,u in U.items():
    t=time.time()
    try:
        if k=="hyperliquid":
            r=urllib.request.urlopen(urllib.request.Request("https://api.hyperliquid.xyz/info",data=b'{"type":"metaAndAssetCtxs"}',headers={"Content-Type":"application/json"}),timeout=15)
        else:
            r=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=15)
        b=r.read()
        print(f"OK   {k:24s} {r.status} {len(b)}B {time.time()-t:.1f}s {b[:90]!r}")
    except Exception as e:
        print(f"FAIL {k:24s} {type(e).__name__}: {str(e)[:120]}")
