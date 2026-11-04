"""
Backtest engine for PROTOCOL_TSMOM_v1 (frozen, SHA-256 in PROTOCOL_TSMOM_v1.sha256).

Development data only: every row at or after 2025-01-01 00:00 UTC is dropped at
load time and an assertion guards it. Nothing here reads holdout rows.

Accounting: additive returns on a constant reference capital of 1.0. Each trade's
notional is a fraction of that reference (protocol section 4). This avoids
compounding path-dependence and leaves Sharpe, t-stats and hit rates unchanged.
"""
import os
import numpy as np
import pandas as pd

DATA = os.environ.get("BYBIT_DATA", "bybit_data")  # folder made by data_tools/bybit_download.py
DEV_END_MS = int(pd.Timestamp("2025-01-01", tz="UTC").value // 10**6)
DAY_MS, HOUR_MS = 86_400_000, 3_600_000

FEE = 0.00055          # taker fee per fill
SLIP = 0.0005          # slippage per fill
SUBSYSTEMS = [(20, 10), (50, 25), (100, 50)]
WARMUP_DAYS = 100
VOL_DAYS = 30
SCALE = 0.50
GROSS_CAP = 2.0


# --------------------------------------------------------------------------- #
class Market:
    """Hourly + daily arrays for one symbol, development period only."""

    def __init__(self, sym):
        self.sym = sym
        k = pd.read_csv(os.path.join(DATA, f"{sym}_60.csv.gz"))
        k = k[k.open_time_ms < DEV_END_MS].reset_index(drop=True)
        assert k.open_time_ms.max() < DEV_END_MS
        k["day"] = k.open_time_ms // DAY_MS
        full = k.groupby("day").day.transform("size") == 24          # whole UTC days only
        k = k[full].reset_index(drop=True)
        assert (np.diff(k.open_time_ms) == HOUR_MS).all(), "unexpected hourly gap"

        self.ts = k.open_time_ms.to_numpy()
        self.Oh = k.open.to_numpy(float)
        self.Hh = k.high.to_numpy(float)
        self.Lh = k.low.to_numpy(float)
        self.Ch = k.close.to_numpy(float)

        g = k.groupby("day", sort=True)
        self.days = np.array(sorted(k.day.unique()))
        self.hs = g.head(1).index.to_numpy()                          # first hourly idx of each day
        self.H = g.high.max().to_numpy(float)
        self.L = g.low.min().to_numpy(float)
        self.C = g.close.last().to_numpy(float)
        self.de = self.hs + 23                                        # last hourly idx of each day

        # sigma known at the close of day d (annualised, last 30 daily returns)
        r = np.full(len(self.C), np.nan)
        r[1:] = self.C[1:] / self.C[:-1] - 1
        self.sigma = pd.Series(r).rolling(VOL_DAYS).std().to_numpy() * np.sqrt(365)

        # funding: cumulative (rate x price) on the hourly grid; a payment at hour h
        # is charged to positions with entry_idx < h <= exit_idx
        f = pd.read_csv(os.path.join(DATA, f"{sym}_funding.csv.gz"))
        f = f[f.funding_time_ms < DEV_END_MS]
        fr = np.zeros(len(self.ts))
        idx = np.searchsorted(self.ts, f.funding_time_ms.to_numpy())
        ok = (idx < len(self.ts)) & (self.ts[np.minimum(idx, len(self.ts) - 1)] == f.funding_time_ms.to_numpy())
        self.funding_unmatched = int((~ok).sum())
        np.add.at(fr, idx[ok], f.funding_rate.to_numpy(float)[ok] * self.Oh[idx[ok]])
        self.cumF = np.cumsum(fr)


# --------------------------------------------------------------------------- #
def run_stream(m, n_entry, n_exit, allow_short):
    """One Donchian sub-system on one symbol. Returns a list of trade dicts
    (timing and prices only; size is assigned later)."""
    trades, pos = [], 0
    stop = entry_idx = entry_px = sig = sday = None
    nd = len(m.days)
    for d in range(nd):
        # 1) intraday: stop check on every hourly bar of day d
        if pos != 0:
            h0, h1 = m.hs[d], m.hs[d] + 24
            if entry_idx > h0:              # cannot happen: entries are at day open
                raise AssertionError
            if pos > 0:
                hit = np.nonzero(m.Lh[h0:h1] <= stop)[0]
            else:
                hit = np.nonzero(m.Hh[h0:h1] >= stop)[0]
            if hit.size:
                j = h0 + hit[0]
                raw = min(stop, m.Oh[j]) if pos > 0 else max(stop, m.Oh[j])
                trades.append(dict(sym=m.sym, n=n_entry, side=pos, sday=sday, sigma=sig,
                                   entry_idx=entry_idx, entry_px=entry_px, exit_idx=j,
                                   exit_px=raw * (1 - SLIP * pos), reason="stop"))
                pos = 0

        # 2) daily close of day d: trail the stop, then look for a new signal
        if pos != 0:
            if pos > 0:
                stop = max(stop, m.L[d - n_exit + 1:d + 1].min())
            else:
                stop = min(stop, m.H[d - n_exit + 1:d + 1].max())
        if pos == 0 and d >= WARMUP_DAYS and d + 1 < nd and np.isfinite(m.sigma[d]):
            hi = m.H[d - n_entry:d].max()
            lo = m.L[d - n_entry:d].min()
            side = 1 if m.C[d] > hi else (-1 if (allow_short and m.C[d] < lo) else 0)
            if side:
                pos, sday, sig = side, d, m.sigma[d]
                entry_idx = m.hs[d + 1]
                entry_px = m.Oh[entry_idx] * (1 + SLIP * side)
                stop = (m.L[d - n_exit + 1:d + 1].min() if side > 0
                        else m.H[d - n_exit + 1:d + 1].max())

    if pos != 0:  # end of development data: forced exit at last close, with costs
        j = len(m.ts) - 1
        trades.append(dict(sym=m.sym, n=n_entry, side=pos, sday=sday, sigma=sig,
                           entry_idx=entry_idx, entry_px=entry_px, exit_idx=j,
                           exit_px=m.Ch[j] * (1 - SLIP * pos), reason="end"))
    return trades


def generate_trades(markets, allow_short):
    out = []
    for m in markets.values():
        for n_entry, n_exit in SUBSYSTEMS:
            out += run_stream(m, n_entry, n_exit, allow_short)
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
def size_trades(tr, n_symbols, markets):
    """Notional per protocol, with the 2.0x gross cap applied in entry order."""
    tr = tr.copy()
    tr["entry_ts"] = [markets[s].ts[i] for s, i in zip(tr.sym, tr.entry_idx)]
    tr["exit_ts"] = [markets[s].ts[i] for s, i in zip(tr.sym, tr.exit_idx)]
    tr = tr.sort_values(["entry_ts", "sym", "n"]).reset_index(drop=True)
    tr["notional"] = (1 / 3) * (SCALE / n_symbols) / tr.sigma
    capped, open_ = 0, []          # (exit_ts, notional)
    notional = tr.notional.to_numpy().copy()
    for i, (ets, xts) in enumerate(zip(tr.entry_ts, tr.exit_ts)):
        open_ = [(x, q) for x, q in open_ if x > ets]
        gross = sum(q for _, q in open_)
        if gross + notional[i] > GROSS_CAP:
            notional[i] = max(0.0, GROSS_CAP - gross)
            capped += 1
        open_.append((xts, notional[i]))
    tr["notional"] = notional
    tr.attrs["capped"] = capped
    return tr


def daily_pnl(tr, markets, cost_mult=1.0):
    """Daily P&L (UTC day number -> P&L) and per-trade net P&L.
    cost_mult scales fees AND slippage (slippage is re-applied from raw prices)."""
    pnl = {}
    net = np.zeros(len(tr))
    parts = np.zeros((len(tr), 4))     # price, fees, funding, short/long marker
    for t, r in enumerate(tr.itertuples()):
        m = markets[r.sym]
        s = r.side
        # rebuild fills at the requested cost level
        raw_in = r.entry_px / (1 + SLIP * s)
        raw_out = r.exit_px / (1 - SLIP * s)
        px_in = raw_in * (1 + SLIP * cost_mult * s)
        px_out = raw_out * (1 - SLIP * cost_mult * s)
        u = r.notional / raw_in
        e_day = int(np.searchsorted(m.hs, r.entry_idx, side="right") - 1)
        x_day = int(np.searchsorted(m.hs, r.exit_idx, side="right") - 1)
        fee_in = FEE * cost_mult * u * px_in
        fee_out = FEE * cost_mult * u * px_out

        def add(d, v):
            key = int(m.days[d])
            pnl[key] = pnl.get(key, 0.0) + v

        if e_day == x_day:
            price = s * u * (px_out - px_in)
            fund = -s * u * (m.cumF[r.exit_idx] - m.cumF[r.entry_idx])
            add(e_day, price + fund - fee_in - fee_out)
        else:
            price = 0.0
            fund_total = 0.0
            # entry day
            v = s * u * (m.C[e_day] - px_in)
            f = -s * u * (m.cumF[m.de[e_day]] - m.cumF[r.entry_idx])
            add(e_day, v + f - fee_in); price += v; fund_total += f
            for d in range(e_day + 1, x_day):
                v = s * u * (m.C[d] - m.C[d - 1])
                f = -s * u * (m.cumF[m.de[d]] - m.cumF[m.de[d - 1]])
                add(d, v + f); price += v; fund_total += f
            v = s * u * (px_out - m.C[x_day - 1])
            f = -s * u * (m.cumF[r.exit_idx] - m.cumF[m.de[x_day - 1]])
            add(x_day, v + f - fee_out); price += v; fund_total += f
            fund = fund_total
        net[t] = price + fund - fee_in - fee_out
        parts[t] = (price, fee_in + fee_out, fund, s)
    return pnl, net, parts


def to_series(pnl_dict, start_day, end_day):
    idx = np.arange(start_day, end_day + 1)
    return pd.Series([pnl_dict.get(int(d), 0.0) for d in idx], index=idx)


# --------------------------------------------------------------------------- #
def block_bootstrap_mean(x, block=20, reps=10_000, seed=7):
    x = np.asarray(x)
    n = len(x)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(reps, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(reps, -1)[:, :n]
    return x[idx].mean(axis=1)


def stats(daily):
    r = daily.to_numpy()
    eq = np.cumsum(r)
    dd = eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    mu, sd = r.mean(), r.std(ddof=1)
    return dict(days=len(r), ann_ret=mu * 365, ann_vol=sd * np.sqrt(365),
                sharpe=mu / sd * np.sqrt(365) if sd > 0 else np.nan,
                max_dd=dd.min(), total=eq[-1])


def trade_stats(net):
    w, l = net[net > 0], net[net <= 0]
    return dict(trades=len(net), win_rate=len(w) / len(net) if len(net) else np.nan,
                payoff=(w.mean() / -l.mean()) if len(w) and len(l) else np.nan,
                profit_factor=w.sum() / -l.sum() if len(l) else np.nan)


# --------------------------------------------------------------------------- #
def placebo_totals(tr, markets, n_symbols, reps=1000, seed=11):
    """Random-timing placebo: per (symbol, sub-system) stream keep the trade count,
    each trade's holding duration and side, but draw non-overlapping entry days at
    random within the stream's tradable window. Exit at the hourly open after the
    same duration. Same sizing rule and costs, no stops."""
    rng = np.random.default_rng(seed)
    totals = np.zeros(reps)
    for (sym, n), g in tr.groupby(["sym", "n"]):
        m = markets[sym]
        dur = (g.exit_idx - g.entry_idx).to_numpy()
        side = g.side.to_numpy()
        K = len(dur)
        len_days = dur // 24 + 1
        first_day = WARMUP_DAYS + 1
        n_days = len(m.days) - first_day
        free = n_days - len_days.sum()
        if free < 0:
            raise AssertionError("placebo cannot fit trades")
        sig_entry = np.concatenate([[np.nan], m.sigma[:-1]])   # known at prior close
        for rpt in range(reps):
            perm = rng.permutation(K)
            d_, s_, L_ = dur[perm], side[perm], len_days[perm]
            gaps = np.sort(rng.integers(0, free + 1, size=K))
            gaps = np.diff(np.concatenate([[0], gaps]))
            start = first_day + np.cumsum(gaps) + np.concatenate([[0], np.cumsum(L_)[:-1]])
            ei = m.hs[start]
            xi = ei + d_
            sig = sig_entry[start]
            notional = (1 / 3) * (SCALE / n_symbols) / sig
            pin = m.Oh[ei] * (1 + SLIP * s_)
            pout = m.Oh[xi] * (1 - SLIP * s_)
            u = notional / m.Oh[ei]
            pnl = s_ * u * (pout - pin) - FEE * u * (pin + pout) \
                - s_ * u * (m.cumF[xi] - m.cumF[ei])
            totals[rpt] += pnl.sum()
    return totals


def buy_and_hold(markets, syms, start_day, end_day):
    """Equal-risk long benchmark, weights from the prior close's 30-day vol,
    rebalanced daily, no costs. Only its Sharpe is used."""
    n = len(syms)
    out = to_series({}, start_day, end_day) * 0.0
    for s in syms:
        m = markets[s]
        r = pd.Series(np.r_[np.nan, m.C[1:] / m.C[:-1] - 1], index=m.days)
        w = pd.Series(np.r_[np.nan, (SCALE / n) / m.sigma[:-1]], index=m.days)
        contrib = (r * w).reindex(out.index).fillna(0.0)
        out = out.add(contrib, fill_value=0.0)
    return out
