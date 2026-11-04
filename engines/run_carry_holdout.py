"""One-time holdout for PROTOCOL_CARRY_v1: 2025-01-01 to end of data, rules unchanged."""
import glob, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_carry as C, engine as E
HO = int(pd.Timestamp("2025-01-01", tz="UTC").value // 10**6)
dev = json.load(open("results/carry_dev_results.json"))
syms = sorted(os.path.basename(f).split("_")[0] for f in glob.glob(C.DATA + "/*_spot_60.csv.gz"))
DATA = {s: C.load(s, HO, 10**14) for s in syms}
res = {}
for cname, ss in {"BTC+ETH": ["BTCUSDT", "ETHUSDT"], "27-coin": syms}.items():
    for v in ("conditional", "always_on"):
        series = []
        for s in ss:
            h, f = DATA[s]; C.prepare(h)
            x, _ = C.run(s, h, f, len(ss), 1.0, v == "always_on"); series.append(x)
        d = pd.concat(series, axis=1).sort_index().fillna(0.0).sum(axis=1)
        d = d.loc[d.index.min() + 7:]
        st = E.stats(d); dmax = dev[f"{cname}|{v}"]["stats"]["max_dd"]
        dts = pd.to_datetime(d.index * C.DAY_MS, unit="ms")
        res[f"{cname}|{v}"] = dict(window=[str(dts[0].date()), str(dts[-1].date())], stats=st,
            dev_ann=dev[f"{cname}|{v}"]["stats"]["ann_ret"], dev_maxdd=dmax,
            by_quarter={str(q): round(float(g.sum()), 4) for q, g in d.groupby(dts.to_period("Q"))},
            gates=dict(net_positive=bool(st["total"] > 0), maxdd_ok=bool(st["max_dd"] > 1.5 * dmax)))
        print(cname, v, json.dumps(res[f"{cname}|{v}"], default=float))
json.dump(res, open("results/carry_holdout_results.json", "w"), indent=1, default=float)
