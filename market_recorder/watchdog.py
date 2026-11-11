"""Daily data-health check. Exits non-zero (-> GitHub emails the repo owner) if any recorder is falling behind."""
import json, os, subprocess, sys, time, glob, csv, datetime as dt
repo=os.environ["GITHUB_REPOSITORY"]; now=time.time(); since=dt.datetime.utcfromtimestamp(now-86400).strftime("%Y-%m-%dT%H:%M:%SZ")
def runs(wf):
    out=subprocess.check_output(["gh","api",f"repos/{repo}/actions/workflows/{wf}/runs?created=>{since}&per_page=100"])
    r=json.loads(out)["workflow_runs"]; return len(r), sum(x["conclusion"]=="success" for x in r)
EXPECT={"recorder-loop.yml":3,"market-history.yml":2,"deribit-backfill.yml":1}   # 24h minimums (well below schedule)
problems=[]; lines=[]
for wf,mn in EXPECT.items():
    n,ok=runs(wf); lines.append(f"{wf}: {ok}/{n} successful runs in 24h (min {mn})")
    if ok<mn: problems.append(lines[-1])
# data freshness
def newest(pattern):
    fs=glob.glob(pattern, recursive=True); return max((os.path.getmtime(x) for x in fs), default=0)
snap=sorted(glob.glob("data/market/snap/*/*.csv.gz"))
if snap:
    d,h=snap[-1].split("/")[-2:]; age=(now-dt.datetime.strptime(d+h[:4],"%Y-%m-%d%H%M").replace(tzinfo=dt.timezone.utc).timestamp())/3600
    lines.append(f"latest market snapshot {d} {h[:4]} UTC ({age:.1f}h old)")
    if age>1.5: problems.append(lines[-1])
else: problems.append("no market snapshots yet")
if os.path.exists("data/market/health.csv"):
    rows=list(csv.DictReader(open("data/market/health.csv")))[-7*12:]
    bad={}
    for r in rows:
        if r["error"] or int(r["rows"])==0: bad[r["ex"]]=bad.get(r["ex"],0)+1
    for ex,k in bad.items():
        lines.append(f"{ex}: {k} failed pulls in last ~12 runs")
        if k>=6: problems.append(lines[-1])
import glob as _g
for cur in ("BTC","ETH"):
    fs=sorted(_g.glob(f"data/deribit/trades_daily/{cur}/*.csv.gz"))
    y=(dt.datetime.utcfromtimestamp(now)-dt.timedelta(days=2)).strftime("%Y-%m-%d")
    lines.append(f"deribit {cur} daily tape: {len(fs)} days, latest {fs[-1][-17:-7] if fs else None}")
    if not fs or fs[-1][-17:-7]<y: problems.append(lines[-1])
bk=sorted(_g.glob("data/deribit/*/*/*/*_book.csv.gz"))
lines.append(f"deribit book snapshots saved: {len(bk)}")
if not bk: problems.append("no deribit book snapshots in repo")
st="data/deribit/state.json"
if os.path.exists(st): lines.append("deribit state: "+open(st).read().strip()[:200])
print("\n".join(lines))
if problems: print("\nPROBLEMS:\n"+"\n".join(problems)); sys.exit(1)
print("\nALL OK")
