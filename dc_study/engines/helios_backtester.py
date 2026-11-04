#!/usr/bin/env python3
"""Pine-compatible backtester for HELIOS DC BUG HARVEST v3.

This module has two deliberately separate responsibilities:

1. ``pine-parity`` reproduces the historical TradingView strategy, including
   its delayed orders, partially protected legacy position, 100% margin,
   commission, slippage, quantity rounding, and forced-liquidation fragments.
2. Later research can use the same signal engine with safer execution modes,
   but those modes must never be mixed into the frozen parity baseline.

The implementation uses only the Python standard library and streams zipped
one-minute CSV files, so it can process the complete Bybit history without
loading millions of rows into memory.
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import math
import sys
import zipfile
from collections import deque
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Deque, Iterable, Iterator, Literal


Side = Literal[-1, 1]


@dataclass(frozen=True)
class Bar:
    timestamp: str
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


@dataclass
class Instrument:
    symbol: str
    tick_size: float
    qty_step: float
    min_qty: float
    min_notional: float
    # TradingView's forced-liquidation quantity precision does not behave like
    # Bybit's normal order quantity precision for ETHUSDT.P. These calibrated
    # values reproduce the observed TradingView liquidation fragments.
    margin_qty_step: float


@dataclass
class HeliosConfig:
    initial_capital: float = 1000.0
    commission_pct: float = 0.055
    slippage_ticks: int = 2
    margin_percent: float = 100.0

    threshold_percent: float = 3.0
    smoothing_period: int = 14
    sma_length: int = 200
    signal_source: str = "Raw close (legacy)"

    stricter_short: bool = True
    rsi_length: int = 14
    short_rsi_threshold: float = 40.0
    volume_length: int = 20
    volume_multiplier: float = 1.5

    long_risk_percent: float = 1.0
    long_reward_percent: float = 2.0
    short_risk_percent: float = 0.8
    short_reward_percent: float = 1.5

    legacy_long_recovery_percent: float = 1.0
    legacy_short_recovery_percent: float = 0.75
    fee_per_side_pct: float = 0.055
    desired_net_edge_pct: float = 0.05
    catastrophic_stop_pct: float = 3.0
    max_hold_bars: int = 720
    close_recovery_on_opposite_dc: bool = True

    exit_mode: str = "Legacy bug replay"
    calc_on_order_fills: bool = False

    @property
    def commission_rate(self) -> float:
        return self.commission_pct / 100.0

    @property
    def threshold(self) -> float:
        return self.threshold_percent / 100.0

    @property
    def round_trip_fee_pct(self) -> float:
        return 2.0 * self.fee_per_side_pct

    @property
    def burst_exit_threshold_pct(self) -> float:
        return self.round_trip_fee_pct + self.desired_net_edge_pct

    @property
    def cost_aware_long_recovery_pct(self) -> float:
        return self.long_risk_percent + 2.0 * (
            self.round_trip_fee_pct + self.desired_net_edge_pct
        )

    @property
    def cost_aware_short_recovery_pct(self) -> float:
        return self.short_risk_percent + 2.0 * (
            self.round_trip_fee_pct + self.desired_net_edge_pct
        )


@dataclass
class SignalResult:
    buy_signal: bool
    sell_signal: bool
    long_filter: bool
    short_filter: bool
    trend_direction: int
    sma: float | None
    rsi: float | None
    volume_ma: float | None


class RollingMean:
    def __init__(self, length: int) -> None:
        self.length = length
        self.values: Deque[float] = deque()
        self.total = 0.0

    def update(self, value: float) -> float | None:
        self.values.append(value)
        self.total += value
        if len(self.values) > self.length:
            self.total -= self.values.popleft()
        if len(self.values) < self.length:
            return None
        return self.total / self.length


class PineRsi:
    """TradingView ta.rsi(): change followed by Wilder's RMA."""

    def __init__(self, length: int) -> None:
        self.length = length
        self.previous_close: float | None = None
        self.seed_gains: list[float] = []
        self.seed_losses: list[float] = []
        self.average_gain: float | None = None
        self.average_loss: float | None = None

    def update(self, close: float) -> float | None:
        if self.previous_close is None:
            self.previous_close = close
            return None

        change = close - self.previous_close
        self.previous_close = close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if self.average_gain is None or self.average_loss is None:
            self.seed_gains.append(gain)
            self.seed_losses.append(loss)
            if len(self.seed_gains) < self.length:
                return None
            self.average_gain = sum(self.seed_gains) / self.length
            self.average_loss = sum(self.seed_losses) / self.length
        else:
            self.average_gain = (
                self.average_gain * (self.length - 1) + gain
            ) / self.length
            self.average_loss = (
                self.average_loss * (self.length - 1) + loss
            ) / self.length

        if self.average_loss == 0.0:
            return 100.0
        ratio = self.average_gain / self.average_loss
        return 100.0 - 100.0 / (1.0 + ratio)


class HeliosSignalEngine:
    """Literal translation of the Pine direction-change state machine."""

    def __init__(self, config: HeliosConfig) -> None:
        self.config = config
        self.close_sma = RollingMean(config.sma_length)
        self.smooth_sma = RollingMean(config.smoothing_period)
        self.volume_sma = RollingMean(config.volume_length)
        self.rsi = PineRsi(config.rsi_length)
        self.last_high: float | None = None
        self.last_low: float | None = None
        self.trend_direction = 0

    def update(self, bar: Bar) -> SignalResult:
        smoothed = self.smooth_sma.update(bar.close)
        trend_sma = self.close_sma.update(bar.close)
        volume_ma = self.volume_sma.update(bar.volume)
        rsi = self.rsi.update(bar.close)

        if self.last_high is None and self.last_low is None:
            self.last_high = bar.high
            self.last_low = bar.low

        previous_direction = self.trend_direction
        buy_signal = False
        sell_signal = False
        direction_price = (
            bar.close
            if self.config.signal_source == "Raw close (legacy)"
            else smoothed
        )

        if (
            direction_price is not None
            and self.trend_direction >= 0
            and direction_price
            < float(self.last_high) * (1.0 - self.config.threshold)
        ):
            self.trend_direction = -1
            self.last_low = bar.low
            sell_signal = previous_direction == 1

        # This is intentionally a second independent if, not an elif.
        if (
            direction_price is not None
            and self.trend_direction <= 0
            and direction_price
            > float(self.last_low) * (1.0 + self.config.threshold)
        ):
            self.trend_direction = 1
            self.last_high = bar.high
            buy_signal = previous_direction == -1

        if self.trend_direction == 1 and bar.high > float(self.last_high):
            self.last_high = bar.high
        if self.trend_direction == -1 and bar.low < float(self.last_low):
            self.last_low = bar.low

        long_filter = trend_sma is not None and bar.close > trend_sma
        base_short = trend_sma is not None and bar.close < trend_sma
        strict_short = (
            rsi is not None
            and rsi < self.config.short_rsi_threshold
            and volume_ma is not None
            and bar.volume > volume_ma * self.config.volume_multiplier
        )
        short_filter = base_short and (
            not self.config.stricter_short or strict_short
        )

        return SignalResult(
            buy_signal=buy_signal,
            sell_signal=sell_signal,
            long_filter=long_filter,
            short_filter=short_filter,
            trend_direction=self.trend_direction,
            sma=trend_sma,
            rsi=rsi,
            volume_ma=volume_ma,
        )


@dataclass
class Position:
    side: Side
    qty: float
    original_qty: float
    entry_price: float
    entry_time: str
    entry_bar_index: int


@dataclass
class ExitLeg:
    order_id: str
    qty: float
    stop: float | None = None
    limit: float | None = None
    created_index: int = 0


@dataclass
class PendingMarket:
    action: Literal["entry", "close"]
    side: Side | None
    reason: str


@dataclass
class TradeFragment:
    number: int
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    entry_commission: float
    exit_commission: float
    net_pnl: float
    exit_reason: str


@dataclass
class BacktestResult:
    symbol: str
    start: str
    end: str
    initial_capital: float
    cash: float
    mark_price: float | None
    equity_before_hypothetical_exit_fee: float
    liquidation_value: float
    closed_net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    trades: list[TradeFragment] = field(default_factory=list)
    open_position: dict | None = None
    signals: list[dict] = field(default_factory=list)


def decimal_floor(value: float, step: float) -> float:
    if value <= 0.0:
        return 0.0
    units = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(
        rounding=ROUND_FLOOR
    )
    return float(units * Decimal(str(step)))


def price_floor(value: float, tick: float) -> float:
    units = (Decimal(str(value)) / Decimal(str(tick))).to_integral_value(
        rounding=ROUND_FLOOR
    )
    return float(units * Decimal(str(tick)))


def price_ceil(value: float, tick: float) -> float:
    units = (Decimal(str(value)) / Decimal(str(tick))).to_integral_value(
        rounding=ROUND_CEILING
    )
    return float(units * Decimal(str(tick)))


class PineBroker:
    """Small broker emulator for the order types used by HELIOS."""

    def __init__(
        self,
        instrument: Instrument,
        config: HeliosConfig,
        collect_signals: bool = False,
    ) -> None:
        self.instrument = instrument
        self.config = config
        self.signal_engine = HeliosSignalEngine(config)
        self.cash = config.initial_capital
        self.position: Position | None = None
        self.exit_legs: dict[str, ExitLeg] = {}
        self.filled_exit_ids: set[str] = set()
        self.pending_markets: list[PendingMarket] = []
        self.trades: list[TradeFragment] = []
        self.collect_signals = collect_signals
        self.signals: list[dict] = []
        self.bar_index = -1
        self.last_price: float | None = None

    @property
    def fee_rate(self) -> float:
        return self.config.commission_rate

    @property
    def slip(self) -> float:
        return self.config.slippage_ticks * self.instrument.tick_size

    def _market_price(self, raw_price: float, order_side: Side) -> float:
        return raw_price + order_side * self.slip

    def _position_unrealized(self, current_price: float) -> float:
        if self.position is None:
            return 0.0
        return (
            self.position.side
            * (current_price - self.position.entry_price)
            * self.position.qty
        )

    def equity(self, current_price: float) -> float:
        return self.cash + self._position_unrealized(current_price)

    def _close_fragment(
        self,
        qty: float,
        exit_price: float,
        exit_time: str,
        reason: str,
    ) -> None:
        if self.position is None or qty <= 0.0:
            return
        qty = min(qty, self.position.qty)
        entry_price = self.position.entry_price
        gross = self.position.side * (exit_price - entry_price) * qty
        entry_fee = entry_price * qty * self.fee_rate
        exit_fee = exit_price * qty * self.fee_rate
        net = gross - entry_fee - exit_fee

        # The complete entry commission was charged when the position opened.
        # Here only realized price PnL and the exit commission change cash.
        self.cash += gross - exit_fee
        self.trades.append(
            TradeFragment(
                number=len(self.trades) + 1,
                side="long" if self.position.side == 1 else "short",
                entry_time=self.position.entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                gross_pnl=gross,
                entry_commission=entry_fee,
                exit_commission=exit_fee,
                net_pnl=net,
                exit_reason=reason,
            )
        )
        self.position.qty = max(0.0, self.position.qty - qty)
        if self.position.qty < self.instrument.qty_step / 2.0:
            self.position = None
            self.exit_legs.clear()
            self.filled_exit_ids.clear()

    def _entry_quantity(self, fill_price: float) -> float:
        # TradingView reduces percent-of-equity sizing so entry commission fits
        # inside available equity. This reproduces 0.43 ETH and 889.9 XRP.
        affordable = self.cash / (fill_price * (1.0 + self.fee_rate))
        return decimal_floor(affordable, self.instrument.qty_step)

    def _fill_entry(self, side: Side, raw_price: float, timestamp: str) -> None:
        fill_price = self._market_price(raw_price, side)

        if self.position is not None and self.position.side == side:
            return  # pyramiding = 0

        if self.position is not None and self.position.side != side:
            self._close_fragment(
                self.position.qty,
                fill_price,
                timestamp,
                "reversal",
            )

        qty = self._entry_quantity(fill_price)
        if qty < self.instrument.min_qty:
            return
        if qty * fill_price < self.instrument.min_notional:
            return

        self.cash -= qty * fill_price * self.fee_rate
        self.position = Position(
            side=side,
            qty=qty,
            original_qty=qty,
            entry_price=fill_price,
            entry_time=timestamp,
            entry_bar_index=self.bar_index,
        )
        self.exit_legs.clear()
        self.filled_exit_ids.clear()

    def _fill_pending_at_open(self, bar: Bar) -> bool:
        filled_entry = False
        pending = self.pending_markets
        self.pending_markets = []
        for order in pending:
            if order.action == "entry" and order.side is not None:
                self._fill_entry(order.side, bar.open, bar.timestamp)
                filled_entry = True
            elif order.action == "close" and self.position is not None:
                order_side: Side = -self.position.side  # type: ignore[assignment]
                price = self._market_price(bar.open, order_side)
                self._close_fragment(
                    self.position.qty,
                    price,
                    bar.timestamp,
                    order.reason,
                )
        return filled_entry

    def _margin_call(self, raw_price: float, timestamp: str) -> None:
        if self.position is None or self.config.margin_percent <= 0.0:
            return
        margin_ratio = self.config.margin_percent / 100.0
        market_value = self.position.qty * raw_price
        required_margin = market_value * margin_ratio
        available = self.equity(raw_price) - required_margin
        if available >= 0.0:
            return

        loss = -available / margin_ratio
        cover_raw = loss / raw_price
        cover = decimal_floor(cover_raw, self.instrument.margin_qty_step)
        if cover <= 0.0:
            cover = self.instrument.margin_qty_step
        call_qty = 4.0 * cover
        # TradingView closes a residual below its forced-liquidation precision.
        if self.position.qty - call_qty < self.instrument.margin_qty_step:
            call_qty = self.position.qty
        else:
            call_qty = min(call_qty, self.position.qty)

        order_side: Side = -self.position.side  # type: ignore[assignment]
        fill_price = self._market_price(raw_price, order_side)
        self._close_fragment(
            call_qty,
            fill_price,
            timestamp,
            "margin_call",
        )

    def _active_reserved_qty(self) -> float:
        return sum(leg.qty for leg in self.exit_legs.values())

    def _ensure_exit_leg(
        self,
        order_id: str,
        requested_qty: float,
        stop: float | None,
        limit: float | None,
    ) -> None:
        if self.position is None:
            return
        # A strategy.exit ID that has completed its reserved slice does not
        # repeatedly consume the legacy residual when the Pine call appears on
        # later bars. It can modify a still-open order, but not recreate a
        # completed slice for the same entry.
        if order_id in self.filled_exit_ids:
            return
        existing = self.exit_legs.get(order_id)
        if existing is not None:
            existing.stop = stop
            existing.limit = limit
            existing.qty = min(existing.qty, self.position.qty)
            return

        available = max(0.0, self.position.qty - self._active_reserved_qty())
        qty = min(requested_qty, available)
        qty = decimal_floor(qty, self.instrument.qty_step)
        if qty <= 0.0:
            return
        self.exit_legs[order_id] = ExitLeg(
            order_id=order_id,
            qty=qty,
            stop=stop,
            limit=limit,
            created_index=self.bar_index,
        )

    def _round_exit_prices(
        self,
        side: Side,
        stop_raw: float | None,
        limit_raw: float | None,
    ) -> tuple[float | None, float | None]:
        if side == 1:  # long exits are sell orders
            stop = (
                price_floor(stop_raw, self.instrument.tick_size)
                if stop_raw is not None
                else None
            )
            limit = (
                price_ceil(limit_raw, self.instrument.tick_size)
                if limit_raw is not None
                else None
            )
        else:  # short exits are buy orders
            stop = (
                price_ceil(stop_raw, self.instrument.tick_size)
                if stop_raw is not None
                else None
            )
            limit = (
                price_floor(limit_raw, self.instrument.tick_size)
                if limit_raw is not None
                else None
            )
        return stop, limit

    def _configure_exits(self, bar: Bar, signal: SignalResult) -> None:
        if self.position is None:
            return
        p = self.position
        half_qty = decimal_floor(
            p.original_qty * 0.5,
            self.instrument.qty_step,
        )

        if p.side == 1:
            core_stop_raw = p.entry_price * (
                1.0 - self.config.long_risk_percent / 100.0
            )
            core_target_raw = p.entry_price * (
                1.0 + self.config.long_reward_percent / 100.0
            )
            legacy_recovery_raw = p.entry_price * (
                1.0 + self.config.legacy_long_recovery_percent / 100.0
            )
            capped_recovery_raw = p.entry_price * (
                1.0 + self.config.cost_aware_long_recovery_pct / 100.0
            )
            catastrophic_raw = p.entry_price * (
                1.0 - self.config.catastrophic_stop_pct / 100.0
            )
        else:
            core_stop_raw = p.entry_price * (
                1.0 + self.config.short_risk_percent / 100.0
            )
            core_target_raw = p.entry_price * (
                1.0 - self.config.short_reward_percent / 100.0
            )
            legacy_recovery_raw = p.entry_price * (
                1.0 - self.config.legacy_short_recovery_percent / 100.0
            )
            capped_recovery_raw = p.entry_price * (
                1.0 - self.config.cost_aware_short_recovery_pct / 100.0
            )
            catastrophic_raw = p.entry_price * (
                1.0 + self.config.catastrophic_stop_pct / 100.0
            )

        core_stop, core_target = self._round_exit_prices(
            p.side, core_stop_raw, core_target_raw
        )
        recovery_stop, recovery_target = self._round_exit_prices(
            p.side, catastrophic_raw, capped_recovery_raw
        )
        _, legacy_target = self._round_exit_prices(
            p.side, None, legacy_recovery_raw
        )

        mode = self.config.exit_mode
        if mode == "Full bracket control":
            self._ensure_exit_leg(
                f"{'Long' if p.side == 1 else 'Short'} Full",
                p.original_qty,
                core_stop,
                core_target,
            )
            return

        if mode == "Legacy bug replay":
            prefix = "Long" if p.side == 1 else "Short"
            self._ensure_exit_leg(
                f"{prefix} Core Legacy",
                half_qty,
                core_stop,
                core_target,
            )
            recovery_triggered = (
                bar.high >= legacy_recovery_raw
                if p.side == 1
                else bar.low <= legacy_recovery_raw
            )
            if recovery_triggered:
                self._ensure_exit_leg(
                    f"{prefix} Recovery Legacy",
                    half_qty,
                    None,
                    legacy_target,
                )
            return

        if mode == "One-bar burst diagnostic":
            held_bars = self.bar_index - p.entry_bar_index
            gross_pct = (
                (bar.close / p.entry_price - 1.0) * 100.0
                if p.side == 1
                else (1.0 - bar.close / p.entry_price) * 100.0
            )
            if held_bars == 0 and gross_pct >= self.config.burst_exit_threshold_pct:
                self.pending_markets.append(
                    PendingMarket("close", None, "One-bar burst")
                )
            else:
                self._ensure_exit_leg(
                    f"{'Long' if p.side == 1 else 'Short'} Burst Bracket",
                    p.original_qty,
                    core_stop,
                    core_target,
                )
            return

        # Capped recovery.
        prefix = "Long" if p.side == 1 else "Short"
        self._ensure_exit_leg(
            f"{prefix} Core Capped",
            half_qty,
            core_stop,
            core_target,
        )
        self._ensure_exit_leg(
            f"{prefix} Recovery Capped",
            half_qty,
            recovery_stop,
            recovery_target,
        )
        held_bars = self.bar_index - p.entry_bar_index
        opposite = signal.sell_signal if p.side == 1 else signal.buy_signal
        if self.config.close_recovery_on_opposite_dc and opposite:
            self.pending_markets.append(PendingMarket("close", None, "Opposite DC"))
        elif held_bars >= self.config.max_hold_bars:
            self.pending_markets.append(PendingMarket("close", None, "Time stop"))

    def _fill_leg(self, leg: ExitLeg, price: float, time: str, suffix: str) -> None:
        if self.position is None:
            return
        self._close_fragment(
            leg.qty,
            price,
            time,
            f"{leg.order_id}:{suffix}",
        )
        self.exit_legs.pop(leg.order_id, None)
        self.filled_exit_ids.add(leg.order_id)

    def _process_open_gap(self, bar: Bar) -> None:
        if self.position is None:
            return
        for leg in list(self.exit_legs.values()):
            if self.position is None:
                break
            side = self.position.side
            if side == 1:
                if leg.limit is not None and bar.open >= leg.limit:
                    self._fill_leg(leg, bar.open, bar.timestamp, "limit_gap")
                elif leg.stop is not None and bar.open <= leg.stop:
                    self._fill_leg(
                        leg,
                        self._market_price(bar.open, -1),
                        bar.timestamp,
                        "stop_gap",
                    )
            else:
                if leg.limit is not None and bar.open <= leg.limit:
                    self._fill_leg(leg, bar.open, bar.timestamp, "limit_gap")
                elif leg.stop is not None and bar.open >= leg.stop:
                    self._fill_leg(
                        leg,
                        self._market_price(bar.open, 1),
                        bar.timestamp,
                        "stop_gap",
                    )

    def _segment_events(
        self, start: float, end: float
    ) -> list[tuple[float, ExitLeg, str]]:
        if self.position is None or start == end:
            return []
        events: list[tuple[float, ExitLeg, str]] = []
        upward = end > start
        side = self.position.side

        for leg in self.exit_legs.values():
            if side == 1 and upward and leg.limit is not None:
                if start < leg.limit <= end:
                    events.append((leg.limit, leg, "limit"))
            elif side == 1 and not upward and leg.stop is not None:
                if end <= leg.stop < start:
                    events.append((leg.stop, leg, "stop"))
            elif side == -1 and not upward and leg.limit is not None:
                if end <= leg.limit < start:
                    events.append((leg.limit, leg, "limit"))
            elif side == -1 and upward and leg.stop is not None:
                if start < leg.stop <= end:
                    events.append((leg.stop, leg, "stop"))

        events.sort(key=lambda item: item[0], reverse=not upward)
        return events

    def _process_segment(self, start: float, end: float, timestamp: str) -> None:
        for level, leg, kind in self._segment_events(start, end):
            if self.position is None or leg.order_id not in self.exit_legs:
                continue
            if kind == "limit":
                price = level
            else:
                order_side: Side = -self.position.side  # type: ignore[assignment]
                price = self._market_price(level, order_side)
            self._fill_leg(leg, price, timestamp, kind)
        self._margin_call(end, timestamp)

    @staticmethod
    def _path(bar: Bar) -> list[float]:
        if abs(bar.open - bar.high) < abs(bar.open - bar.low):
            return [bar.open, bar.high, bar.low, bar.close]
        return [bar.open, bar.low, bar.high, bar.close]

    def _execute_strategy_at_close(self, bar: Bar) -> None:
        signal = self.signal_engine.update(bar)
        if self.collect_signals and (signal.buy_signal or signal.sell_signal):
            self.signals.append(
                {
                    "timestamp": bar.timestamp,
                    "buy_signal": signal.buy_signal,
                    "sell_signal": signal.sell_signal,
                    "long_filter": signal.long_filter,
                    "short_filter": signal.short_filter,
                    "trend_direction": signal.trend_direction,
                    "sma": signal.sma,
                    "rsi": signal.rsi,
                }
            )

        # Preserve Pine call order: Long entry call precedes Short entry call.
        if signal.buy_signal and signal.long_filter:
            self.pending_markets.append(PendingMarket("entry", 1, "Long"))
        if signal.sell_signal and signal.short_filter:
            self.pending_markets.append(PendingMarket("entry", -1, "Short"))

        self._configure_exits(bar, signal)

    def process_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        self.last_price = bar.close

        filled_entry = self._fill_pending_at_open(bar)
        self._process_open_gap(bar)
        self._margin_call(bar.open, bar.timestamp)

        # The frozen Pine file sets calc_on_order_fills=false. The optional flag
        # exists only as a diagnostic for TradingView property overrides.
        if filled_entry and self.config.calc_on_order_fills:
            dummy = SignalResult(False, False, False, False, 0, None, None, None)
            self._configure_exits(bar, dummy)

        path = self._path(bar)
        for start, end in zip(path, path[1:]):
            self._process_segment(start, end, bar.timestamp)

        self._execute_strategy_at_close(bar)

    def result(self, symbol: str, start: str, end: str) -> BacktestResult:
        mark = self.last_price
        unrealized = self._position_unrealized(mark) if mark is not None else 0.0
        equity = self.cash + unrealized
        liquidation_value = equity
        open_info: dict | None = None
        if self.position is not None and mark is not None:
            hypothetical_exit_fee = self.position.qty * mark * self.fee_rate
            liquidation_value -= hypothetical_exit_fee
            open_info = {
                "side": "long" if self.position.side == 1 else "short",
                "qty": self.position.qty,
                "original_qty": self.position.original_qty,
                "entry_time": self.position.entry_time,
                "entry_price": self.position.entry_price,
                "mark_price": mark,
                "net_pnl_if_closed_at_mark": (
                    self.position.side
                    * (mark - self.position.entry_price)
                    * self.position.qty
                    - self.position.entry_price
                    * self.position.qty
                    * self.fee_rate
                    - hypothetical_exit_fee
                ),
            }

        closed_net = sum(t.net_pnl for t in self.trades)
        profits = sum(t.net_pnl for t in self.trades if t.net_pnl > 0.0)
        losses = -sum(t.net_pnl for t in self.trades if t.net_pnl < 0.0)
        pf = profits / losses if losses > 0.0 else None
        return BacktestResult(
            symbol=symbol,
            start=start,
            end=end,
            initial_capital=self.config.initial_capital,
            cash=self.cash,
            mark_price=mark,
            equity_before_hypothetical_exit_fee=equity,
            liquidation_value=liquidation_value,
            closed_net_pnl=closed_net,
            gross_profit=profits,
            gross_loss=losses,
            profit_factor=pf,
            trades=self.trades,
            open_position=open_info,
            signals=self.signals,
        )


def resolve_year_zip(data_dir: Path, symbol: str, year: int) -> Path:
    candidates = [
        Path(path)
        for path in glob.glob(str(data_dir / f"{symbol}_1m_{year}.csv*.zip"))
    ]
    if not candidates:
        raise FileNotFoundError(f"No {symbol} {year} ZIP found in {data_dir}")
    # When both the small parity sample and full 2026 file exist, select full.
    return max(candidates, key=lambda path: path.stat().st_size)


def iter_bars_from_zip(
    path: Path,
    start: str | None = None,
    end: str | None = None,
) -> Iterator[Bar]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1:
            raise ValueError(f"Expected one CSV in {path}, found {len(names)}")
        with archive.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            for row in csv.DictReader(text):
                timestamp = row["timestamp_utc"]
                if start is not None and timestamp < start:
                    continue
                if end is not None and timestamp >= end:
                    break
                yield Bar(
                    timestamp=timestamp,
                    timestamp_ms=int(row["timestamp_ms"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume_base"]),
                    turnover=float(row["turnover_usdt"]),
                )


def years_in_range(start: str, end: str) -> range:
    return range(int(start[:4]), int(end[:4]) + 1)


def iter_history(
    data_dir: Path,
    symbol: str,
    start: str,
    end: str,
) -> Iterable[Bar]:
    for year in years_in_range(start, end):
        try:
            path = resolve_year_zip(data_dir, symbol, year)
        except FileNotFoundError:
            continue
        yield from iter_bars_from_zip(path, start=start, end=end)


def resolve_instrument(data_dir: Path, symbol: str) -> Instrument:
    candidates = list(data_dir.glob(f"{symbol}_instrument*.json"))
    if not candidates:
        raise FileNotFoundError(f"No {symbol} instrument JSON found in {data_dir}")
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = json.loads(path.read_text(encoding="utf-8"))
    lot = payload["lotSizeFilter"]
    price = payload["priceFilter"]
    qty_step = float(lot["qtyStep"])
    margin_step = 0.1 if symbol in {"ETHUSDT", "XRPUSDT"} else qty_step
    return Instrument(
        symbol=symbol,
        tick_size=float(price["tickSize"]),
        qty_step=qty_step,
        min_qty=float(lot["minOrderQty"]),
        min_notional=float(lot["minNotionalValue"]),
        margin_qty_step=margin_step,
    )


def run_backtest(
    data_dir: Path,
    symbol: str,
    start: str,
    end: str,
    config: HeliosConfig,
    collect_signals: bool = False,
) -> BacktestResult:
    instrument = resolve_instrument(data_dir, symbol)
    broker = PineBroker(instrument, config, collect_signals=collect_signals)
    count = 0
    for bar in iter_history(data_dir, symbol, start, end):
        broker.process_bar(bar)
        count += 1
    if count == 0:
        raise RuntimeError("No bars found for the requested interval")
    return broker.result(symbol, start, end)


def result_to_dict(result: BacktestResult) -> dict:
    payload = asdict(result)
    return payload


def write_trade_csv(path: Path, result: BacktestResult) -> None:
    fields = list(asdict(result.trades[0]).keys()) if result.trades else [
        "number",
        "side",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "qty",
        "gross_pnl",
        "entry_commission",
        "exit_commission",
        "net_pnl",
        "exit_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(asdict(trade))


def print_summary(result: BacktestResult) -> None:
    print(f"{result.symbol}: {result.start} to {result.end}")
    print(f"Closed fragments: {len(result.trades)}")
    print(f"Closed net PnL: {result.closed_net_pnl:.6f} USDT")
    print(f"Liquidation-value PnL: {result.liquidation_value - result.initial_capital:.6f} USDT")
    print(
        "Profit factor: "
        + (f"{result.profit_factor:.6f}" if result.profit_factor is not None else "n/a")
    )
    if result.open_position:
        print("Open position: " + json.dumps(result.open_position, sort_keys=True))
    print()
    for trade in result.trades:
        print(
            f"{trade.number:2d} {trade.side:5s} {trade.entry_time} "
            f"{trade.entry_price:.8f} -> {trade.exit_time} "
            f"{trade.exit_price:.8f} qty={trade.qty:g} "
            f"net={trade.net_pnl:.6f} {trade.exit_reason}"
        )


PARITY_CASES = {
    "ETHUSDT": {
        "start": "2026-08-17T00:00:00Z",
        "end": "2026-08-22T19:17:00Z",
        "closed_net_pnl": 18.050361,
        "profit_factor": 2.243872,
        "open_qty": None,
        "trades": [
            ("long", "2026-08-20T09:07:00Z", "2026-08-20T11:49:00Z", 2292.15, 2315.08, 0.21, 4.283),
            ("long", "2026-08-20T09:07:00Z", "2026-08-20T12:09:00Z", 2292.15, 2269.20, 0.21, -5.346),
            ("long", "2026-08-20T09:07:00Z", "2026-08-21T11:20:00Z", 2292.15, 2374.26, 0.01, 0.795),
            ("short", "2026-08-21T11:20:00Z", "2026-08-21T11:20:00Z", 2374.26, 2380.63, 0.42, -3.774),
            ("long", "2026-08-21T18:20:00Z", "2026-08-21T18:45:00Z", 2427.22, 2402.92, 0.20, -5.391),
            ("long", "2026-08-21T18:20:00Z", "2026-08-21T21:12:00Z", 2427.22, 2451.50, 0.20, 4.319),
            ("long", "2026-08-21T18:20:00Z", "2026-08-22T05:10:00Z", 2427.22, 2451.64, 0.01, 0.217),
            ("short", "2026-08-22T05:10:00Z", "2026-08-22T05:11:00Z", 2451.64, 2391.61, 0.20, 11.473),
            ("short", "2026-08-22T05:10:00Z", "2026-08-22T05:11:00Z", 2451.64, 2391.61, 0.20, 11.473),
        ],
    },
    "XRPUSDT": {
        # The screenshot was captured during the 18:53 candle. Closed fills
        # remain exactly reproducible from finalized data; its displayed open
        # trade used the then-live mark of 1.4781 rather than the final close.
        "start": "2026-08-17T00:00:00Z",
        "end": "2026-08-22T18:54:00Z",
        "closed_net_pnl": 41.670979,
        "profit_factor": None,
        "open_qty": 350.7,
        "trades": [
            ("long", "2026-08-20T08:11:00Z", "2026-08-20T08:19:00Z", 1.1231, 1.1344, 444.9, 4.475),
            ("long", "2026-08-20T08:11:00Z", "2026-08-20T08:31:00Z", 1.1231, 1.1456, 444.9, 9.455),
            ("long", "2026-08-20T08:11:00Z", "2026-08-22T10:03:00Z", 1.1231, 1.4841, 0.1, 0.036),
            ("short", "2026-08-22T10:03:00Z", "2026-08-22T10:03:00Z", 1.4841, 1.4845, 0.4, -0.001),
            ("short", "2026-08-22T10:03:00Z", "2026-08-22T10:03:00Z", 1.4841, 1.4894, 16.8, -0.117),
            ("short", "2026-08-22T10:03:00Z", "2026-08-22T10:05:00Z", 1.4841, 1.4729, 324.2, 3.104),
            ("short", "2026-08-22T10:03:00Z", "2026-08-22T10:09:00Z", 1.4841, 1.4618, 341.4, 7.060),
            ("short", "2026-08-22T14:18:00Z", "2026-08-22T14:18:00Z", 1.4795, 1.4799, 0.4, -0.001),
            ("short", "2026-08-22T14:18:00Z", "2026-08-22T14:18:00Z", 1.4795, 1.4829, 10.0, -0.050),
            ("short", "2026-08-22T14:18:00Z", "2026-08-22T14:40:00Z", 1.4795, 1.4607, 335.5, 5.765),
            ("short", "2026-08-22T14:18:00Z", "2026-08-22T14:41:00Z", 1.4795, 1.4573, 345.8, 7.118),
            ("long", "2026-08-22T16:41:00Z", "2026-08-22T18:22:00Z", 1.4775, 1.4929, 350.6, 4.826),
        ],
    },
}


def verify_parity(data_dir: Path) -> tuple[bool, dict]:
    report: dict = {
        "strategy": "HELIOS DC BUG HARVEST v3",
        "status": "passed",
        "cases": {},
    }
    all_passed = True

    for symbol, case in PARITY_CASES.items():
        result = run_backtest(
            data_dir=data_dir,
            symbol=symbol,
            start=case["start"],
            end=case["end"],
            config=HeliosConfig(),
            collect_signals=True,
        )
        errors: list[str] = []
        expected_trades = case["trades"]
        if len(result.trades) != len(expected_trades):
            errors.append(
                f"trade count {len(result.trades)} != {len(expected_trades)}"
            )

        for index, (actual, expected) in enumerate(
            zip(result.trades, expected_trades), start=1
        ):
            (
                side,
                entry_time,
                exit_time,
                entry_price,
                exit_price,
                qty,
                displayed_net,
            ) = expected
            exact_fields = {
                "side": (actual.side, side),
                "entry_time": (actual.entry_time, entry_time),
                "exit_time": (actual.exit_time, exit_time),
            }
            for name, (observed, wanted) in exact_fields.items():
                if observed != wanted:
                    errors.append(
                        f"trade {index} {name}: {observed!r} != {wanted!r}"
                    )
            numeric_fields = {
                "entry_price": (actual.entry_price, entry_price),
                "exit_price": (actual.exit_price, exit_price),
                "qty": (actual.qty, qty),
            }
            for name, (observed, wanted) in numeric_fields.items():
                if not math.isclose(observed, wanted, abs_tol=1e-9):
                    errors.append(
                        f"trade {index} {name}: {observed} != {wanted}"
                    )
            if round(actual.net_pnl, 3) != displayed_net:
                errors.append(
                    f"trade {index} displayed net: "
                    f"{round(actual.net_pnl, 3)} != {displayed_net}"
                )

        if not math.isclose(
            result.closed_net_pnl,
            case["closed_net_pnl"],
            abs_tol=1e-6,
        ):
            errors.append(
                f"closed net {result.closed_net_pnl:.6f} != "
                f"{case['closed_net_pnl']:.6f}"
            )

        expected_open = case["open_qty"]
        actual_open = (
            result.open_position["qty"] if result.open_position is not None else None
        )
        if expected_open is None and actual_open is not None:
            errors.append(f"unexpected open position qty {actual_open}")
        elif expected_open is not None and (
            actual_open is None
            or not math.isclose(actual_open, expected_open, abs_tol=1e-9)
        ):
            errors.append(f"open qty {actual_open} != {expected_open}")

        case_passed = not errors
        all_passed = all_passed and case_passed
        report["cases"][symbol] = {
            "status": "passed" if case_passed else "failed",
            "errors": errors,
            "closed_fragments": len(result.trades),
            "closed_net_pnl": result.closed_net_pnl,
            "profit_factor": result.profit_factor,
            "open_position": result.open_position,
            "signals": result.signals,
            "trades": [asdict(trade) for trade in result.trades],
        }

    report["status"] = "passed" if all_passed else "failed"
    return all_passed, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pine-compatible HELIOS DC BUG HARVEST v3 backtester"
    )
    parser.add_argument("--data-dir", type=Path, default=Path("upload"))
    parser.add_argument("--symbol", choices=["ETHUSDT", "XRPUSDT"])
    parser.add_argument("--start", default="2026-08-17T00:00:00Z")
    parser.add_argument("--end", default="2026-08-22T19:17:00Z")
    parser.add_argument(
        "--exit-mode",
        choices=[
            "Legacy bug replay",
            "One-bar burst diagnostic",
            "Capped recovery",
            "Full bracket control",
        ],
        default="Legacy bug replay",
    )
    parser.add_argument(
        "--calc-on-order-fills",
        action="store_true",
        help="Diagnostic override; the frozen Pine source sets this false.",
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--trades-out", type=Path)
    parser.add_argument("--collect-signals", action="store_true")
    parser.add_argument(
        "--verify-parity",
        action="store_true",
        help="Verify both symbols against the frozen TradingView trade lists.",
    )
    parser.add_argument(
        "--parity-report",
        type=Path,
        help="Optional JSON path for the complete parity evidence.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.verify_parity:
        passed, report = verify_parity(args.data_dir)
        print(f"Pine parity: {'PASS' if passed else 'FAIL'}")
        for symbol, case in report["cases"].items():
            print(
                f"{symbol}: {case['status'].upper()}, "
                f"fragments={case['closed_fragments']}, "
                f"closed_net={case['closed_net_pnl']:.6f}"
            )
            for error in case["errors"]:
                print(f"  - {error}")
        if args.parity_report:
            args.parity_report.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return 0 if passed else 1

    if args.symbol is None:
        print("Error: --symbol is required unless --verify-parity is used.", file=sys.stderr)
        return 2
    config = HeliosConfig(
        exit_mode=args.exit_mode,
        calc_on_order_fills=args.calc_on_order_fills,
    )
    result = run_backtest(
        data_dir=args.data_dir,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        config=config,
        collect_signals=args.collect_signals,
    )
    print_summary(result)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(result_to_dict(result), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.trades_out:
        write_trade_csv(args.trades_out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
