"""Development evaluation for PROTOCOL_VRP_v1 (frozen). Writes results/vrp_dev_results.json."""
import json, math, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_vrp as V
import engine as E

DEV_END = int(pd.Timestamp("2025-01-01", tz="UTC").value // 10**6)
LAST_EXPIRY = DEV_END - 16 * V.HOUR_MS          # 2024-12-31 08:00 UTC
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

res, daily = {}, {}
for cur in ("BTC", "ETH"):
    p, v, f = V.load(cur, DEV_END)
    # Part A
    snap = V.snapshots(p, v)
    c = p.close.to_numpy(float); t_idx = p.open_time_ms.to_numpy()
    lr = np.diff(np.log(c))
    csum = np.concatenate([[0], np.cumsum(lr ** 2)])
    rows = []
    for t, dv in zip(snap.open_time_ms, snap.dvol):
        i0 = np.searchsorted(t_idx, t)          # bar starting at t; returns from close[i0-1]
        if i0 < 1 or i0 - 1 + 720 > len(lr):
            continue
        rv = math.sqrt((csum[i0 - 1 + 720] - csum[i0 - 1]) * 8760 / 720) * 100
        rows.append((t, dv, rv))
    a = pd.DataFrame(rows, columns=["t", "iv", "rv"])
    a["prem"] = a.iv - a.rv
    a["vprem"] = (a.iv / 100) ** 2 - (a.rv / 100) ** 2
    a["year"] = pd.to_datetime(a.t, unit="ms").dt.year
    res[f"{cur}|partA"] = dict(
        days=len(a), mean_vol_pts=a.prem.mean(), median_vol_pts=a.prem.median(),
        share_positive=(a.prem > 0).mean(), worst_1pct=a.prem.quantile(0.01),
        mean_var_prem=a.vprem.mean(),
        by_year=a.groupby("year").prem.agg(["mean", "median", lambda x: (x > 0).mean()]).round(2)
                 .rename(columns={"<lambda_0>": "share_pos"}).to_dict("index"))
    # Part B
    for label, hc, hs in (("base", 2.0, 1.0), ("stress", 4.0, 2.0)):
        b = V.part_b(p, v, f, LAST_EXPIRY, iv_haircut=hc, half_spread=hs)
        daily[(cur, label)] = b.set_index("t").ret
        comp = {k: float((b[k] / b.S.shift().fillna(b.S)).sum())
                for k in ("opt", "hedge", "fund", "fees", "spread", "hedge_cost")}
        res[f"{cur}|partB|{label}"] = dict(stats=E.stats(b.set_index("t").ret), components=comp,
                                          first=str(pd.to_datetime(b.t.iloc[0], unit="ms")),
                                          last=str(pd.to_datetime(b.t.iloc[-1], unit="ms")))
    print(cur, "done", flush=True)

for label in ("base", "stress"):
    d = pd.concat([daily[("BTC", label)], daily[("ETH", label)]], axis=1).sort_index().fillna(0.0).mean(axis=1)
    st = E.stats(d)
    boots = E.block_bootstrap_mean(d.to_numpy(), block=30)
    dts = pd.to_datetime(d.index, unit="ms")
    years = {int(y): float(g.sum()) for y, g in d.groupby(dts.year)}
    worst30 = float(d.rolling(30).sum().min())
    res[f"COMBINED|{label}"] = dict(stats=st, boot_lower=float(np.percentile(boots, 2.5)),
                                    boot_p=float((boots <= 0).mean()), years=years,
                                    worst_day=float(d.min()), worst_30d=worst30)
    d.to_csv(os.path.join(OUT, f"vrp_dev_daily_{label}.csv"))

base, stress = res["COMBINED|base"], res["COMBINED|stress"]
res["gates"] = dict(
    g1_bootstrap=base["boot_lower"] > 0,
    g2_stress=stress["stats"]["total"] > 0,
    g3_years=sum(base["years"].get(y, 0) > 0 for y in (2021, 2022, 2023, 2024)) >= 3,
    g4_drawdown=-base["stats"]["max_dd"] < 3 * base["stats"]["ann_ret"])
res["gates"] = {k: bool(v) for k, v in res["gates"].items()}
json.dump(res, open(os.path.join(OUT, "vrp_dev_results.json"), "w"), indent=1, default=float)
print(json.dumps(res["gates"]))
