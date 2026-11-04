"""Funding-carry engine for PROTOCOL_CARRY_v1 (frozen). Long spot + short perp, equal units."""
import os
import numpy as np
import pandas as pd

DATA = os.environ.get("BYBIT_DATA", "bybit_data")  # folder made by data_tools/bybit_download.py
DAY_MS, HOUR_MS = 86_400_000, 3_600_000
SPOT_FEE, PERP_FEE, SLIP = 0.0010, 0.00055, 0.0005
ENTER, EXIT = 0.15, 0.0
SPOT_MULT = {"SHIB1000USDT": 1000.0}


def load(sym, t_from_ms, t_to_ms, data=DATA):
    """Hourly perp+spot aligned on common hours, funding, restricted to [t_from, t_to)."""
    p = pd.read_csv(os.path.join(data, f"{sym}_60.csv.gz"))
    s = pd.read_csv(os.path.join(data, f"{sym}_spot_60.csv.gz"))
    f = pd.read_csv(os.path.join(data, f"{sym}_funding.csv.gz"))
    k = lambda d, c: d[(d[c] >= t_from_ms) & (d[c] < t_to_ms)]
    p, s, f = k(p, "open_time_ms"), k(s, "open_time_ms"), k(f, "funding_time_ms")
    m = SPOT_MULT.get(sym, 1.0)
    h = p[["open_time_ms", "open", "high", "close"]].merge(
        s[["open_time_ms", "open", "close"]], on="open_time_ms", suffixes=("_p", "_s"))
    h[["open_s", "close_s"]] *= m
    assert h.open_time_ms.max() < t_to_ms
    return h.reset_index(drop=True), f.reset_index(drop=True)


def run(sym, h, f, n_coins, cost_mult=1.0, always_on=False):
    """Daily marks at 00:00 opens. Returns (daily P&L Series indexed by day number, trade list)."""
    h = h[h.open_time_ms % DAY_MS == 0]                     # 00:00 bars only
    days = (h.open_time_ms // DAY_MS).to_numpy()
    S, P = h.open_s.to_numpy(), h.open_p.to_numpy()
    T = h.open_time_ms.to_numpy()
    fts, frate = f.funding_time_ms.to_numpy(), f.funding_rate.to_numpy(float)
    # perp price at each funding time: perp hourly open at that hour (from the full hourly frame)
    sf, pf, sl = SPOT_FEE * cost_mult, PERP_FEE * cost_mult, SLIP * cost_mult
    pnl = np.zeros(len(days))
    trades = []
    pos = False
    u = S0 = P0 = None
    first = None
    for d in range(len(days)):
        t = T[d]
        win = (fts >= t - 7 * DAY_MS) & (fts < t)
        f7 = frate[win].sum() * 365 / 7
        # protocol: 7-day warm-up after the first date with spot, perp and funding
        warm = fts.size and fts.min() <= t - 7 * DAY_MS and t >= T[0] + 7 * DAY_MS
        if pos:
            # mark both legs from previous 00:00 to this 00:00, plus funding received
            pnl[d] += u * ((S[d] - S[d - 1]) - (P[d] - P[d - 1]))
            last = d == len(days) - 1
            exiting = (not always_on and f7 < EXIT) or last
            # funding in (previous 00:00, this 00:00]; the payment at the exit instant
            # itself is conservatively not received
            got = (fts > T[d - 1]) & ((fts < t) if exiting else (fts <= t)) & (fts > entry_t)
            pnl[d] += u * (frate[got] * fund_px(fts[got])).sum()
            if exiting:
                xs, xp = S[d] * (1 - sl), P[d] * (1 + sl)
                pnl[d] += u * ((xs - S[d]) - (xp - P[d])) - u * (sf * xs + pf * xp)
                trades.append(dict(sym=sym, entry_day=int(days[e_d]), exit_day=int(days[d]),
                                   u=u, notional=1.0 / n_coins, max_perp_adverse=max_adv(e_d, d)))
                pos = False
                continue
        if not pos and warm and d < len(days) - 1 and (always_on and first is None or (not always_on and f7 > ENTER)):
            first = d
            es, ep = S[d] * (1 + sl), P[d] * (1 - sl)
            u = (1.0 / n_coins) / S[d]
            pnl[d] += u * ((S[d] - es) - (P[d] - ep)) - u * (sf * es + pf * ep)
            pos, e_d, entry_t = True, d, t
    return pd.Series(pnl, index=days), trades


# helpers bound per call via closure-free globals set by prepare()
_HOURLY = {}


def fund_px(ts):
    hp = _HOURLY["hp"]
    i = np.searchsorted(hp[:, 0], ts)
    i = np.clip(i, 0, len(hp) - 1)
    ok = hp[i, 0] == ts
    px = hp[i, 1].copy()
    px[~ok] = np.nan
    if (~ok).any():   # funding hour missing from the aligned hourly grid: use last known price
        j = np.clip(np.searchsorted(hp[:, 0], ts[~ok]) - 1, 0, len(hp) - 1)
        px[~ok] = hp[j, 1]
    return px


def max_adv(e_d, x_d):
    """Largest rise of perp high over its entry 00:00 open during the hold (short-leg risk)."""
    hh = _HOURLY["hh"]
    t0, t1 = _HOURLY["T"][e_d], _HOURLY["T"][x_d]
    seg = hh[(hh[:, 0] >= t0) & (hh[:, 0] < t1)]
    if not len(seg):
        return 0.0
    return float(seg[:, 1].max() / _HOURLY["P"][e_d] - 1)


def prepare(h):
    _HOURLY["hp"] = h[["open_time_ms", "open_p"]].to_numpy(float)
    _HOURLY["hh"] = h[["open_time_ms", "high"]].to_numpy(float)
    z = h[h.open_time_ms % DAY_MS == 0]
    _HOURLY["T"] = z.open_time_ms.to_numpy()
    _HOURLY["P"] = z.open_p.to_numpy()
