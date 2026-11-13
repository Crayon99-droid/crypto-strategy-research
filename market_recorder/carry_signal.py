"""Daily carry signal: BTC/ETH 7-day funding (OKX + Gate history, ~= Bybit) annualised.
>= ON_APR  -> open a GitHub issue 'CARRY ON' (GitHub emails the repo owner)
<= OFF_APR -> close it with a 'CARRY OFF' comment. Always appends to data/market/carry_signal.csv."""
import os, csv, json, subprocess, time, datetime as dt
ON_APR, OFF_APR = 10.0, 3.0
now=time.time()*1000; week=7*86400*1000
def apr(ex,coin):
    p=f"data/market/hist/funding/{ex}/{coin}.csv"
    if not os.path.exists(p): return None
    rows=[r for r in csv.reader(open(p))][1:]
    rs=[float(r[1]) for r in rows if r and float(r[0])>=now-week]
    return sum(rs)*365/7*100 if len(rs)>=10 else None
vals={f"{c}_{e}":apr(e,c) for c in ("BTC","ETH") for e in ("okx","gate")}
got=[v for v in vals.values() if v is not None]
avg=sum(got)/len(got) if got else None
day=dt.datetime.utcnow().strftime("%Y-%m-%d")
p="data/market/carry_signal.csv"; new=not os.path.exists(p)
with open(p,"a") as f:
    if new: f.write("date,avg_apr,"+",".join(vals)+"\n")
    f.write(day+","+(f"{avg:.2f}" if avg is not None else "")+","+",".join("" if v is None else f"{v:.2f}" for v in vals.values())+"\n")
print("7d funding APR %:",{k:(None if v is None else round(v,2)) for k,v in vals.items()},"avg",avg)
repo=os.environ.get("GITHUB_REPOSITORY")
if not repo or avg is None: raise SystemExit(0)
def gh(*a): return subprocess.run(["gh",*a],capture_output=True,text=True).stdout
open_issues=json.loads(gh("issue","list","-R",repo,"--label","carry-signal","--state","open","--json","number") or "[]")
if avg>=ON_APR and not open_issues:
    gh("label","create","carry-signal","-R",repo,"--color","0e8a16","--force")
    body=(f"BTC/ETH 7-day funding now pays about **{avg:.1f}% a year** (threshold {ON_APR}%).\n\n"
          f"Details: {json.dumps({k:(None if v is None else round(v,1)) for k,v in vals.items()})}\n\n"
          "Cash-and-carry (buy spot, short the same amount of the perp, Bybit Unified account, 1x) is worth switching on. "
          f"This issue closes automatically when funding falls below {OFF_APR}%.")
    gh("issue","create","-R",repo,"--title",f"CARRY ON: BTC/ETH funding {avg:.1f}%/yr","--body",body,"--label","carry-signal")
    print("opened CARRY ON issue")
elif avg<=OFF_APR and open_issues:
    n=str(open_issues[0]["number"])
    gh("issue","comment",n,"-R",repo,"--body",f"CARRY OFF: funding fell to {avg:.1f}% a year. Close the spot + short positions.")
    gh("issue","close",n,"-R",repo); print("closed issue (CARRY OFF)")
