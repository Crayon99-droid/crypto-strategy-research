"""One-time holdout for PROTOCOL_VRP_v1: cycles sold from 2025-01-01 with expiry inside the data."""
import json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_vrp as V, engine as E
HO = int(pd.Timestamp("2025-01-01", tz="UTC").value // 10**6)
dev = json.load(open("results/vrp_dev_results.json"))
res, daily = {}, {}
for cur in ("BTC", "ETH"):
    p, v, f = V.load(cur, 10**14)
    p, v, f = p[p.open_time_ms >= HO], v[v.time_ms >= HO], f[f.funding_time_ms >= HO]
    last_exp = int(p.open_time_ms.max())
    for label, hc, hs in (("base", 2.0, 1.0), ("stress", 4.0, 2.0)):
        b = V.part_b(p.reset_index(drop=True), v.reset_index(drop=True), f.reset_index(drop=True), last_exp, hc, hs)
        daily[(cur, label)] = b.set_index("t").ret
        res[f"{cur}|{label}"] = E.stats(b.set_index("t").ret)
for label in ("base", "stress"):
    d = pd.concat([daily[("BTC", label)], daily[("ETH", label)]], axis=1).sort_index().fillna(0).mean(axis=1)
    st = E.stats(d); dts = pd.to_datetime(d.index, unit="ms")
    res[f"COMBINED|{label}"] = dict(stats=st, worst_day=float(d.min()), worst_30d=float(d.rolling(30).sum().min()),
        by_quarter={str(q): round(float(g.sum()), 4) for q, g in d.groupby(dts.to_period("Q"))},
        window=[str(dts[0].date()), str(dts[-1].date())])
b = res["COMBINED|base"]; dmax = dev["COMBINED|base"]["stats"]["max_dd"]
res["gates"] = dict(net_positive=bool(b["stats"]["total"] > 0), maxdd_ok=bool(b["stats"]["max_dd"] > 1.5 * dmax))
json.dump(res, open("results/vrp_holdout_results.json", "w"), indent=1, default=float)
for k, r in res.items():
    if k.startswith("COMBINED"):
        s = r["stats"]; print(k, r["window"], f"ann {s['ann_ret']:.3f} Sharpe {s['sharpe']:.2f} maxDD {s['max_dd']:.3f} worst day {r['worst_day']:.3f} worst 30d {r['worst_30d']:.3f}"); print("   ", r["by_quarter"])
    elif k != "gates":
        print(k, f"ann {r['ann_ret']:.3f} Sharpe {r['sharpe']:.2f} maxDD {r['max_dd']:.3f}")
print("limit maxDD", round(1.5 * dmax, 3), "GATES", res["gates"])
