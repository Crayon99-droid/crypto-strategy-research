"""Development-period evaluation for PROTOCOL_TSMOM_v1. Writes results/dev_results.json."""
import glob, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import engine as E

OUT = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(OUT, exist_ok=True)

syms = sorted(os.path.basename(f).split("_")[0] for f in glob.glob(E.DATA + "/*_60.csv.gz"))
M = {s: E.Market(s) for s in syms}
TR = {v: E.generate_trades(M, v == "LS") for v in ("LS", "LO")}

CONFIGS = {                       # name: (symbols, decisive?)
    "BTC+ETH": (["BTCUSDT", "ETHUSDT"], True),
    "30-coin": (syms, True),
    "BTC": (["BTCUSDT"], False),
    "ETH": (["ETHUSDT"], False),
}
results = {}
for cname, (ss, decisive) in CONFIGS.items():
    start = min(int(M[s].days[E.WARMUP_DAYS + 1]) for s in ss)
    end = max(int(M[s].days[-1]) for s in ss)
    bh = E.buy_and_hold(M, ss, start, end)
    bh_stats = E.stats(bh)
    for v in ("LS", "LO"):
        tr = E.size_trades(TR[v][TR[v].sym.isin(ss)], len(ss), M)
        pnl, net, parts = E.daily_pnl(tr, M, 1.0)
        pnl2, net2, _ = E.daily_pnl(tr, M, 2.0)
        daily = E.to_series(pnl, start, end)
        daily2 = E.to_series(pnl2, start, end)
        st, st2 = E.stats(daily), E.stats(daily2)
        boots = E.block_bootstrap_mean(daily.to_numpy())
        lower = np.percentile(boots, 2.5)
        p_boot = float((boots <= 0).mean())
        plac = E.placebo_totals(tr, M, len(ss), reps=1000)
        years = {}
        dts = pd.to_datetime(daily.index * E.DAY_MS, unit="ms")
        for y, g in daily.groupby(dts.year):
            years[int(y)] = float(g.sum())
        long_ = net[tr.side.to_numpy() > 0].sum()
        short_ = net[tr.side.to_numpy() < 0].sum()
        tstats = E.trade_stats(net)
        price, fees, fund = parts[:, 0].sum(), parts[:, 1].sum(), parts[:, 2].sum()
        by_sym = pd.Series(net, index=tr.sym).groupby(level=0).sum()
        by_sub = pd.Series(net, index=tr.n).groupby(level=0).sum()
        g4 = sum(years.get(y, 0) > 0 for y in (2021, 2022, 2023, 2024))
        results[f"{cname}|{v}"] = dict(
            decisive=decisive, symbols=len(ss),
            window=[str(pd.to_datetime(start * E.DAY_MS, unit="ms").date()),
                    str(pd.to_datetime(end * E.DAY_MS, unit="ms").date())],
            stats=st, stats_2x_cost=st2, buy_hold=bh_stats,
            boot_lower_mean_daily=float(lower), boot_p_mean_le_0=p_boot,
            placebo_p95=float(np.percentile(plac, 95)), placebo_median=float(np.median(plac)),
            placebo_rank=float((plac < st["total"]).mean()),
            years=years, positive_years_2021_24=int(g4),
            long_pnl=float(long_), short_pnl=float(short_),
            trades=tstats, price_pnl=float(price), fees=float(fees), funding=float(fund),
            by_symbol=by_sym.round(4).to_dict(), by_subsystem={int(k): float(x) for k, x in by_sub.items()},
            gates=dict(
                g1_bootstrap=bool(lower > 0),
                g2_placebo=bool(st["total"] > np.percentile(plac, 95)),
                g3_double_cost=bool(st2["ann_ret"] > 0),
                g4_years=bool(g4 >= 3),
            ),
        )
        out_tr = tr.assign(net=net)
        out_tr.to_csv(os.path.join(OUT, f"trades_{cname.replace('+','_')}_{v}.csv"), index=False)
        print(cname, v, "done", flush=True)

# gate 5 (drift): LO Sharpe > buy-and-hold Sharpe, or LS shorts profitable
for cname in CONFIGS:
    lo, ls = results[f"{cname}|LO"], results[f"{cname}|LS"]
    g5_lo = lo["stats"]["sharpe"] > lo["buy_hold"]["sharpe"]
    g5_ls = ls["short_pnl"] > 0
    lo["gates"]["g5_drift"] = bool(g5_lo)
    ls["gates"]["g5_drift"] = bool(g5_ls or g5_lo)

json.dump(results, open(os.path.join(OUT, "dev_results.json"), "w"), indent=1, default=float)
print("saved")
