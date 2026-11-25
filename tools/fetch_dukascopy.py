import lzma, struct, datetime as dt, requests, sys, os, gzip, io
from concurrent.futures import ThreadPoolExecutor
SYMS=['USATECHIDXUSD','USA500IDXUSD']; START=dt.date(2021,1,1); END=dt.date(2026,11,21)
S=requests.Session()
def one(args):
    sym,side,d=args
    url=f'https://datafeed.dukascopy.com/datafeed/{sym}/{d.year}/{d.month-1:02d}/{d.day:02d}/{side}_candles_min_1.bi5'
    for k in range(4):
        try:
            r=S.get(url,timeout=30)
            if r.status_code==404: return []
            r.raise_for_status(); raw=r.content
            if not raw: return []
            b=lzma.decompress(raw); base=int(dt.datetime(d.year,d.month,d.day,tzinfo=dt.timezone.utc).timestamp())
            out=[]
            for i in range(0,len(b),24):
                t,o,c,l,h,v=struct.unpack('>IIIIIf',b[i:i+24]); out.append(f'{base+t},{o},{h},{l},{c},{v:.4f}')
            return out
        except Exception as e: err=e
    print('FAIL',url,err,flush=True); return []
os.makedirs('out',exist_ok=True)
days=[START+dt.timedelta(i) for i in range((END-START).days) if (START+dt.timedelta(i)).weekday()!=5]
for sym in SYMS:
    for side in ['BID','ASK']:
        with ThreadPoolExecutor(24) as ex: res=list(ex.map(one,[(sym,side,d) for d in days]))
        rows=[x for r in res for x in r]
        with gzip.open(f'out/{sym}_{side}_1m.csv.gz','wt') as f: f.write('t,o,h,l,c,v\n'+'\n'.join(rows)+'\n')
        print(sym,side,len(rows),flush=True)
