"""Download Bybit linear USDT public trade archives and aggregate executed taker-side flow to 1m.

This is a stand-alone optional DATA COLLECTION tool, not a profitable strategy and
not a source of Bybit order-book depth. Run where public.bybit.com is accessible.

Windows:
    py -m pip install pandas numpy requests
    py h8b_download_orderflow.py --start 2024-04-01 --days 7 --symbols BTCUSDT ETHUSDT XRPUSDT

It saves compact 1m taker_buy_usdt, taker_sell_usdt, cvd_usdt, and a SHA256 ledger,
then deletes each temporary .csv.gz after successful aggregation.
Archive format is inferred from the header and checked, never silently guessed.
"""
from __future__ import annotations
import argparse, hashlib, json, os, tempfile, time, gzip
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOLS=('BTCUSDT','ETHUSDT','XRPUSDT')

def retrieve_day(symbol,date,outdir,chunk_rows=250_000):
    url=f'https://public.bybit.com/trading/{symbol}/{symbol}{date}.csv.gz'
    print('GET',url,flush=True)
    tmp=outdir/(f'.{symbol}{date}.part')
    sha=hashlib.sha256();size=0
    for tries in range(3):
        try:
            with requests.get(url,stream=True,timeout=(20,120)) as res:
                res.raise_for_status()
                with open(tmp,'wb') as f:
                    for b in res.iter_content(1024*1024):
                        if not b:continue
                        sha.update(b);size+=len(b);f.write(b)
            break
        except (requests.RequestException, OSError):
            tmp.unlink(missing_ok=True);sha=hashlib.sha256();size=0
            if tries==2:raise
            time.sleep(2**tries)
    try:
        with gzip.open(tmp,'rt') as f:
            header=f.readline().strip().split(',')
        expected={'timestamp','side','size','price'}
        if not expected.issubset(header):raise ValueError(f'{url} wrong schema: {header}')
        all_parts=[];total_rows=0
        for d in pd.read_csv(tmp,compression='gzip',usecols=['timestamp','side','size','price'],chunksize=chunk_rows):
            side=d['side'].astype(str)
            if not side.isin(['Buy','Sell']).all():raise ValueError('Unexpected taker side')
            ts=pd.to_numeric(d.timestamp,errors='raise').to_numpy(dtype='float64')
            if (ts<1e9).any() or (ts>1e13).any():raise ValueError('Unexpected epoch time unit')
            seconds=np.where(ts>1e11,ts/1000,ts)
            minutes=np.floor(seconds/60).astype('int64')
            qty=pd.to_numeric(d['size'],errors='raise').to_numpy(dtype='float64')
            price=pd.to_numeric(d['price'],errors='raise').to_numpy(dtype='float64')
            if not np.isfinite(qty).all() or not np.isfinite(price).all() or (qty<=0).any() or (price<=0).any():raise ValueError('Invalid trade size or price')
            usdt=qty*price
            t=pd.DataFrame({'minute':minutes, 'buy_usdt':np.where(side.to_numpy()=='Buy',usdt,0.),
                'sell_usdt':np.where(side.to_numpy()=='Sell',usdt,0.),'trade_count':1})
            all_parts.append(t.groupby('minute',sort=False).sum());total_rows+=len(d)
        if not all_parts:raise ValueError('Empty archive')
        z=pd.concat(all_parts).groupby(level=0).sum().sort_index()
        day_start=int(pd.Timestamp(date,tz='UTC').timestamp()//60)
        expect=np.arange(day_start,day_start+1440,dtype='int64')
        extra=z.index.difference(expect)
        if len(extra):raise ValueError(f'Trades timestamp outside {date}: {extra[:4].tolist()}')
        z=z.reindex(expect,fill_value=0)
        z['symbol']=symbol;z['date']=date;z['minute_utc']=pd.to_datetime(z.index*60,unit='s',utc=True).strftime('%Y-%m-%dT%H:%M:%SZ')
        z['delta_usdt']=z.buy_usdt-z.sell_usdt
        z['taker_buy_fraction']=z.buy_usdt/(z.buy_usdt+z.sell_usdt).replace(0,np.nan)
        dest=outdir/f'{symbol}_orderflow_1m_{date}.csv.gz';z[['minute_utc','buy_usdt','sell_usdt','delta_usdt','trade_count','taker_buy_fraction']].to_csv(dest,index=False,compression='gzip')
        return {'symbol':symbol,'date':date,'url':url,'sha256_compressed_archive':sha.hexdigest(),
          'original_gz_bytes':size,'trade_rows':total_rows,'total_usdt':float((z.buy_usdt+z.sell_usdt).sum()),
          'nonempty_minutes':int((z.trade_count>0).sum()),'output_file':dest.name,'output_sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
    finally:tmp.unlink(missing_ok=True)

def run():
    p=argparse.ArgumentParser();p.add_argument('--start',required=True);p.add_argument('--days',type=int,default=7);p.add_argument('--symbols',nargs='+',choices=SYMBOLS,default=list(SYMBOLS));p.add_argument('--output-dir',default='Bybit_Trade_Flow');a=p.parse_args()
    if a.days<1 or a.days>62:raise ValueError('One bounded download batch must be 1-62 days')
    out=Path(a.output_dir);out.mkdir(exist_ok=True,parents=True)
    first=datetime.strptime(a.start,'%Y-%m-%d').date();ledger=[]
    for sym in a.symbols:
        for j in range(a.days):
            day=(first+timedelta(days=j)).isoformat()
            rec=retrieve_day(sym,day,out);ledger.append(rec)
            (out/'source_ledger.json').write_text(json.dumps(ledger,indent=2))
            print('  Done',sym,day,'trades',rec['trade_rows'],flush=True)
    import zipfile
    with zipfile.ZipFile(out/'ORDERFLOW_UPLOAD_THIS.zip','w',zipfile.ZIP_DEFLATED) as z:
        for rec in ledger:z.write(out/rec['output_file'],rec['output_file'])
        z.write(out/'source_ledger.json','source_ledger.json')
    print('COMPLETE:',out/'ORDERFLOW_UPLOAD_THIS.zip')
if __name__=='__main__':run()
