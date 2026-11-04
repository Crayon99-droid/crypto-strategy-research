"""Volatility-risk-premium screen for PROTOCOL_VRP_v1 (frozen)."""
import math
import os
import numpy as np
import pandas as pd

DATA = os.environ.get("BYBIT_DATA", "bybit_data")  # folder made by data_tools/bybit_download.py
DAY_MS, HOUR_MS = 86_400_000, 3_600_000
OPT_FEE, OPT_CAP, SETTLE_FEE = 0.0003, 0.125, 0.00015
HEDGE_COST = 0.00055 + 0.0005
TENOR_D = 30
Q = 1.0 / TENOR_D

_SQ2 = math.sqrt(2.0)
ncdf = np.vectorize(lambda x: 0.5 * (1.0 + math.erf(x / _SQ2)))


def npdf(x):
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_straddle(S, K, T, sig):
    """Black-Scholes (r=0) straddle value, delta, vega (per 1.00 of sigma), call/put values."""
    S, K, T, sig = map(np.asarray, (S, K, T, sig))
    T = np.maximum(T, 0.0)
    live = T > 0
    out_v = np.where(S > K, S - K, K - S).astype(float)     # intrinsic for expired
    out_d = np.sign(S - K).astype(float)
    out_vega = np.zeros_like(out_v)
    call = np.maximum(S - K, 0.0).astype(float)
    put = np.maximum(K - S, 0.0).astype(float)
    if live.any():
        s, k, t, v = S[live] if S.ndim else S, K[live], T[live], sig[live] if sig.ndim else sig
        sd = v * np.sqrt(t)
        d1 = (np.log(s / k) + 0.5 * sd * sd) / sd
        d2 = d1 - sd
        c = s * ncdf(d1) - k * ncdf(d2)
        p = c - s + k
        out_v[live] = c + p
        out_d[live] = 2 * ncdf(d1) - 1
        out_vega[live] = 2 * s * npdf(d1) * np.sqrt(t)
        call[live], put[live] = c, p
    return out_v, out_d, out_vega, call, put


def load(cur, t_to_ms, data=DATA):
    """Underlying (Bybit perp hourly), DVOL hourly, funding; everything < t_to_ms."""
    sym = f"{cur}USDT"
    p = pd.read_csv(os.path.join(data, f"{sym}_60.csv.gz"))
    p = p[p.open_time_ms < t_to_ms].reset_index(drop=True)
    v = pd.read_csv(os.path.join(data, f"DVOL_{cur}_60.csv.gz"))
    v = v[v.time_ms < t_to_ms].reset_index(drop=True)
    f = pd.read_csv(os.path.join(data, f"{sym}_funding.csv.gz"))
    f = f[f.funding_time_ms < t_to_ms].reset_index(drop=True)
    assert max(p.open_time_ms.max(), v.time_ms.max()) < t_to_ms
    return p, v, f


def snapshots(p, v):
    """Daily 08:00 UTC: underlying open and DVOL (value at 08:00, tolerance 2h back)."""
    s = p[p.open_time_ms % DAY_MS == 8 * HOUR_MS][["open_time_ms", "open"]].rename(columns={"open": "S"})
    vv = v[["time_ms", "open"]].rename(columns={"time_ms": "open_time_ms", "open": "dvol"}).sort_values("open_time_ms")
    s = pd.merge_asof(s.sort_values("open_time_ms"), vv, on="open_time_ms",
                      tolerance=2 * HOUR_MS, direction="backward")
    return s.dropna().reset_index(drop=True)


def realised_vol(p, t_ms, hours=720):
    """Annualised RV from hourly close-to-close log returns over (t, t+hours]."""
    c = p.set_index("open_time_ms").close
    i0 = c.index.searchsorted(t_ms)
    seg = c.iloc[max(i0 - 1, 0): i0 + hours]
    if len(seg) < hours + 1:
        return np.nan
    r = np.diff(np.log(seg.to_numpy()))
    return math.sqrt((r ** 2).sum() * 8760 / hours)


def part_b(p, v, f, last_expiry_ms, iv_haircut=2.0, half_spread=1.0):
    """Daily ladder of short 30-day ATM straddles, daily delta hedge. Returns daily frame."""
    snap = snapshots(p, v)
    snap = snap[snap.open_time_ms <= last_expiry_ms].reset_index(drop=True)
    T, S, IV = snap.open_time_ms.to_numpy(), snap.S.to_numpy(float), (snap.dvol.to_numpy(float) - iv_haircut) / 100
    fts, frate = f.funding_time_ms.to_numpy(), f.funding_rate.to_numpy(float)
    hp = p.set_index("open_time_ms").open
    book = []                          # dicts: K, expiry_ms
    hedge = 0.0
    prev_val = 0.0
    rows = []
    for d in range(len(T)):
        t, s, iv = T[d], S[d], IV[d]
        prem = fees = spread = settle = hedge_pnl = hedge_cost = fund = 0.0
        if d > 0:
            hedge_pnl = hedge * (s - S[d - 1])
            got = (fts > T[d - 1]) & (fts <= t)
            if got.any():
                px = hp.reindex(fts[got]).ffill().bfill().to_numpy()
                fund = -hedge * (frate[got] * px).sum()
        # value existing book at today's snapshot (expiring ones at intrinsic)
        if book:
            K = np.array([b["K"] for b in book]); X = np.array([b["x"] for b in book])
            tau = (X - t) / (365 * DAY_MS)
            val, dlt, _, call, put = bs_straddle(np.full(len(K), s), K, tau, np.full(len(K), iv))
            exp = X <= t
            if exp.any():
                settle = (np.minimum(SETTLE_FEE * s, OPT_CAP * call[exp]) +
                          np.minimum(SETTLE_FEE * s, OPT_CAP * put[exp])).sum() * Q
        else:
            val = np.zeros(0); exp = np.zeros(0, bool)
        book_val_before_sale = Q * val.sum()
        payout = Q * val[exp].sum() if len(val) else 0.0
        book = [b for b, e in zip(book, exp) if not e]
        val_left = Q * val[~exp].sum() if len(val) else 0.0
        dl = (Q * dlt[~exp]).sum() if len(val) else 0.0
        # sell today's straddle if its expiry is inside the allowed window
        x_ms = t + TENOR_D * DAY_MS
        new_val = 0.0
        if x_ms <= last_expiry_ms:
            v0, d0, vega0, c0, p0 = bs_straddle(np.array([s]), np.array([s]), np.array([TENOR_D / 365]), np.array([iv]))
            prem = Q * v0[0]
            fees = Q * (min(OPT_FEE * s, OPT_CAP * c0[0]) + min(OPT_FEE * s, OPT_CAP * p0[0]))
            spread = Q * vega0[0] * half_spread / 100
            book.append({"K": s, "x": x_ms})
            new_val = prem
            dl += Q * d0[0]
        # option P&L today: premium cash in, minus change in liability
        opt_pnl = prem - (book_val_before_sale - prev_val) - new_val
        # re-hedge: short straddles have delta -dl, so hold +dl perp
        hedge_cost = HEDGE_COST * abs(dl - hedge) * s
        hedge = dl
        prev_val = val_left + new_val
        total = opt_pnl + hedge_pnl + fund - fees - spread - settle - hedge_cost
        ref = S[d - 1] if d > 0 else s
        rows.append(dict(t=t, S=s, iv=iv, prem=prem, opt=opt_pnl, hedge=hedge_pnl, fund=fund,
                         fees=fees + settle, spread=spread, hedge_cost=hedge_cost, payout=payout,
                         pnl=total, ret=total / ref, open_contracts=len(book)))
        if not book and x_ms > last_expiry_ms:
            break
    return pd.DataFrame(rows)
