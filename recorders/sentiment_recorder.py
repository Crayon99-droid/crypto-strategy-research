"""Records retail-trader positioning for forex, indices and metals so a history exists for later tests.

Sources (each optional; a source without its secret is skipped):
  Myfxbook community outlook  - needs MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD (free account, 100 calls per 24 h)
  OANDA position book         - needs OANDA_TOKEN (and OANDA_ENV = practice or live)
Appends one row per symbol per run to data/sentiment/<source>/<YYYY-MM-DD>.csv. Never prints secrets.
"""
import csv, datetime as dt, json, os, sys, urllib.parse, urllib.request

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
    for fn in (myfxbook, oanda):
        try:
            status.append(fn())
        except Exception as e:
            status.append(f'{fn.__name__}: ERROR {type(e).__name__}')
    print(NOW.isoformat(), ' | '.join(status))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'recorder_log.txt'), 'a') as f:
        f.write(f'{NOW.isoformat()} {" | ".join(status)}\n')
    sys.exit(0)
