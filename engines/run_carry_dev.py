"""Development evaluation for PROTOCOL_CARRY_v1 (frozen). Writes results/carry_dev_results.json."""
import glob, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_carry as C
import engine as E   # bootstrap + stats helpers

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
DEV_END = int(pd.Timestamp("2025-01-01", tz="UTC").value // 10**6)
syms = sorted(os.path.basename(f).split("_")[0] for f in glob.glob(C.DATA + "/*_spot_60.csv.gz"))
DATA = {s: C.load(s, 0, DEV_END) for s in syms}


def portfolio(ss, cost_mult, always_on):
    series, trades = [], []
    for s in ss:
        h, f = DATA[s]
        C.prepare(h)
        x, tr = C.run(s, h, f, len(ss), cost_mult, always_on)
        series.append(x.rename(s))
        trades += tr
    df = pd.concat(series, axis=1).sort_index().fillna(0.0)
    first = min(x.index.min() for x in series) + 7      # end of 7-day warm-up
    return df.loc[first:], trades


def components(ss, always_on):
    """Funding, basis+fill and fee totals (1x costs), by recomputing without each part."""
    tot = portfolio(ss, 1.0, always_on)[0].sum().sum()
    nocost = portfolio(ss, 0.0, always_on)[0].sum().sum()
    return dict(total=tot, costs=tot - nocost)


res = {}
for cname, ss in {"BTC+ETH": ["BTCUSDT", "ETHUSDT"], "27-coin": syms,
                  "BTC": ["BTCUSDT"], "ETH": ["ETHUSDT"]}.items():
    for v in ("conditional", "always_on"):
        ao = v == "always_on"
        df, trades = portfolio(ss, 1.0, ao)
        df2, _ = portfolio(ss, 2.0, ao)
        d, d2 = df.sum(axis=1), df2.sum(axis=1)
        st, st2 = E.stats(d), E.stats(d2)
        boots = E.block_bootstrap_mean(d.to_numpy(), block=20)
        dts = pd.to_datetime(d.index * C.DAY_MS, unit="ms")
        years = {int(y): float(g.sum()) for y, g in d.groupby(dts.year)}
        pos_share = np.mean([x > 0 for x in years.values()])
        tdf = pd.DataFrame(trades)
        in_mkt = float((df != 0).any(axis=1).mean())
        comp = components(ss, ao)
        res[f"{cname}|{v}"] = dict(
            window=[str(dts[0].date()), str(dts[-1].date())], stats=st, stats_2x=st2,
            worst_day=float(d.min()), boot_lower=float(np.percentile(boots, 2.5)),
            boot_p=float((boots <= 0).mean()), years=years, pos_year_share=float(pos_share),
            trades=len(tdf), time_in_market=in_mkt, cost_total=comp["costs"],
            max_perp_adverse=float(tdf.max_perp_adverse.max()) if len(tdf) else None,
            by_coin=df.sum().round(4).to_dict(),
            gates=dict(g1_bootstrap=bool(np.percentile(boots, 2.5) > 0),
                       g2_double_cost=bool(st2["total"] > 0),
                       g3_years=bool(pos_share >= 0.75),
                       g4_maxdd=bool(st["max_dd"] > -0.10),
                       g5_hurdle=bool(st["ann_ret"] > 0.05)))
        print(cname, v, "done", flush=True)
json.dump(res, open(os.path.join(OUT, "carry_dev_results.json"), "w"), indent=1, default=float)
