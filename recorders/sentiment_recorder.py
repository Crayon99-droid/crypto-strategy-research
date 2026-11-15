"""Records retail-trader positioning for forex, indices and metals so a history exists for later tests.

Open sources (no account): Gate and Bitget publish, for their forex / index / gold perpetuals, the share of
accounts that are long vs short (the same kind of crowd measure as Binance's long/short account ratio).
Sources that need a secret (skipped until it exists):
  Myfxbook community outlook  - needs MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD (free account, 100 calls per 24 h)
  OANDA position book         - needs OANDA_TOKEN (and OANDA_ENV = practice or live)
Appends one row per symbol per run to data/sentiment/<source>/<YYYY-MM-DD>.csv. Never prints secrets.
"""
import csv, datetime as dt, json, os, sys, time, urllib.parse, urllib.request

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
OUT = os.path.join('data', 'sentiment')
OANDA_INSTRUMENTS = ['EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD', 'USD_CAD', 'USD_CHF', 'NZD_USD', 'EUR_JPY', 'GBP_JPY',
                     'XAU_USD', 'XAG_USD', 'NAS100_USD', 'SPX500_USD', 'US30_USD', 'DE30_EUR']


def get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'helios-recorder', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def append(source, header, rows):
    if not rows:
        return
    d = os.path.join(OUT, source)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f'{NOW:%Y-%m-%d}.csv')
    new = not os.path.exists(path)
    with open(path, 'a', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


GATE_CONTRACTS = ['EURUSD_USDT', 'GBPUSD_USDT', 'NAS100_USDT', 'SPX500_USDT', 'GER40_USDT', 'XAU_USDT', 'QQQ_USDT', 'SPY_USDT', 'BTC_USDT']
BITGET_SYMBOLS = ['EURUSDUSDT', 'GBPUSDUSDT', 'USDJPYUSDT', 'NDX100USDT', 'XAUUSDT', 'QQQUSDT', 'SPYUSDT', 'BTCUSDT']


def append_new(source, key, rows, tkey):
    """Append JSON rows newer than the last saved timestamp for this key (gap-safe: each call asks for hours of history)."""
    d = os.path.join(OUT, source)
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, 'state.json')
    state = json.load(open(sp)) if os.path.exists(sp) else {}
    last = float(state.get(key, 0))
    new = sorted((r for r in rows if float(r[tkey]) > last), key=lambda r: float(r[tkey]))
    if new:
        with open(os.path.join(d, f'{NOW:%Y-%m-%d}.jsonl'), 'a') as f:
            for r in new:
                f.write(json.dumps({'key': key, **r}, separators=(',', ':')) + '\n')
        state[key] = float(new[-1][tkey])
        json.dump(state, open(sp, 'w'), indent=0, sort_keys=True)
    return len(new)


def gate():
    n, errs, first = 0, 0, ''
    for c in GATE_CONTRACTS:
        try:
            rows = get('https://api.gateio.ws/api/v4/futures/usdt/contract_stats?' + urllib.parse.urlencode({'contract': c, 'interval': '5m', 'limit': 100}))
            n += append_new('gate', c, rows, 'time')
        except Exception as e:
            errs += 1
            first = first or f'{c} {type(e).__name__} {str(e)[:60]}'
    return f'gate: {n} new rows, {errs} errors {first}'.strip()


def gate_backfill(days):
    """One-off: page Gate's 5-minute stats backwards as far as it serves them (up to `days`)."""
    d = os.path.join(OUT, 'gate_backfill')
    os.makedirs(d, exist_ok=True)
    out = []
    for c in GATE_CONTRACTS:
        seen, end, stop = {}, int(NOW.timestamp()), int(NOW.timestamp()) - days * 86400
        while end > stop:
            try:
                rows = get('https://api.gateio.ws/api/v4/futures/usdt/contract_stats?' + urllib.parse.urlencode(
                    {'contract': c, 'interval': '5m', 'limit': 100, 'from': end - 100 * 300}))
            except Exception as e:
                out.append(f'{c} stopped: {type(e).__name__} {str(e)[:50]}')
                break
            rows = [r for r in rows if r['time'] <= end]
            if not rows:
                break
            for r in rows:
                seen[r['time']] = r
            end = min(r['time'] for r in rows) - 1
            time.sleep(0.15)
        with open(os.path.join(d, c + '.jsonl'), 'w') as f:
            for t in sorted(seen):
                f.write(json.dumps(seen[t], separators=(',', ':')) + '\n')
        first = dt.datetime.fromtimestamp(min(seen), dt.timezone.utc).date() if seen else None
        out.append(f'{c}: {len(seen)} rows from {first}')
    return 'gate_backfill: ' + '; '.join(out)


def bitget():
    n, errs, first = 0, 0, ''
    for s in BITGET_SYMBOLS:
        time.sleep(1.3)          # documented limit: 1 request per second per IP
        try:
            j = get('https://api.bitget.com/api/v2/mix/market/account-long-short?' + urllib.parse.urlencode({'symbol': s, 'period': '5m'}))
            n += append_new('bitget', s, j.get('data') or [], 'ts')
        except Exception as e:
            errs += 1
            first = first or f'{s} {type(e).__name__} {str(e)[:60]}'
    return f'bitget: {n} new rows, {errs} errors {first}'.strip()


def myfxbook():
    email, pw = os.environ.get('MYFXBOOK_EMAIL'), os.environ.get('MYFXBOOK_PASSWORD')
    if not email or not pw:
        return 'myfxbook: skipped (no secret)'
    base = 'https://www.myfxbook.com/api/'
    j = get(base + 'login.json?' + urllib.parse.urlencode({'email': email, 'password': pw}))
    if j.get('error'):
        return 'myfxbook: login failed: ' + str(j.get('message'))[:80]
    s = j['session']          # used as returned; it is already URL-encoded
    try:
        o = get(base + 'get-community-outlook.json?session=' + s)
        if o.get('error'):
            return 'myfxbook: outlook failed: ' + str(o.get('message'))[:80]
        keys = ['name', 'longPercentage', 'shortPercentage', 'longVolume', 'shortVolume', 'longPositions', 'shortPositions',
                'avgLongPrice', 'avgShortPrice']
        rows = [[NOW.isoformat()] + [x.get(k) for k in keys] for x in o.get('symbols', [])]
        append('myfxbook', ['time_utc'] + keys, rows)
        return f'myfxbook: {len(rows)} symbols'
    finally:
        try:
            get(base + 'logout.json?session=' + s)
        except Exception:
            pass


def oanda():
    tok = os.environ.get('OANDA_TOKEN')
    if not tok:
        return 'oanda: skipped (no secret)'
    host = 'api-fxtrade.oanda.com' if os.environ.get('OANDA_ENV', 'practice').lower() == 'live' else 'api-fxpractice.oanda.com'
    rows, errs = [], 0
    for ins in OANDA_INSTRUMENTS:
        try:
            b = get(f'https://{host}/v3/instruments/{ins}/positionBook', {'Authorization': 'Bearer ' + tok})['positionBook']
            lo = sum(float(x['longCountPercent']) for x in b['buckets'])
            sh = sum(float(x['shortCountPercent']) for x in b['buckets'])
            px = float(b['price'])
            la = sum(float(x['longCountPercent']) for x in b['buckets'] if float(x['price']) > px)    # longs opened above price = losing
            sb = sum(float(x['shortCountPercent']) for x in b['buckets'] if float(x['price']) < px)   # shorts opened below price = losing
            rows.append([NOW.isoformat(), ins, b.get('time'), px, round(lo, 4), round(sh, 4), round(la, 4), round(sb, 4)])
        except Exception:
            errs += 1
    append('oanda', ['time_utc', 'instrument', 'book_time', 'price', 'long_pct', 'short_pct', 'long_pct_losing', 'short_pct_losing'], rows)
    return f'oanda: {len(rows)} instruments, {errs} errors'


if __name__ == '__main__':
    status = []
    bf = int(os.environ.get('BACKFILL_DAYS') or 0)
    if bf > 0:
        try:
            status.append(gate_backfill(bf))
        except Exception as e:
            status.append(f'gate_backfill: ERROR {type(e).__name__}')
    for fn in (gate, bitget, myfxbook, oanda):
        try:
            status.append(fn())
        except Exception as e:
            status.append(f'{fn.__name__}: ERROR {type(e).__name__}')
    print(NOW.isoformat(), ' | '.join(status))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'recorder_log.txt'), 'a') as f:
        f.write(f'{NOW.isoformat()} {" | ".join(status)}\n')
    sys.exit(0)
