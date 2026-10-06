"""Event-driven daily portfolio simulator (long only, no leverage).

Timeline for every session *d* (benchmark-index calendar):

1. **Open / intraday** - for each held position with a bar on *d*: execute an exit
   decided at the previous close; otherwise check gaps through stop / targets at
   the open, then intraday stop and targets. When a bar touches both the stop and
   a target, the stop is assumed to fill first (pessimistic).
2. **Entries** - orders placed at the previous close are filled (``next_open``: at
   the open; ``zone_limit``: at the open if it is at/below the limit, else at the
   limit if the low reaches it, otherwise the order expires). Stops/targets are set
   from the actual fill and the signal-day ATR. A new position is also exposed to
   its fill day's intraday stop / target checks.
3. **Close** - trailing stop update, trend-failure / time / max-holding exits are
   decided on the close and executed at the next open. Equity is marked to market.
4. **New orders** - BUY candidates of session *d* (best score first) become orders
   for *d+1*, limited by free position slots.

Every fill pays slippage (against us), commission and exchange fees. Prices are
split/dividend-adjusted, so P&L is a total-return approximation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from bist_quant.backtest.signals import Candidate
from bist_quant.config import Settings
from bist_quant.risk.stop_loss import trailing_stop


@dataclass
class ExitLeg:
    date: pd.Timestamp
    shares: int
    price: float  # effective fill (after slippage)
    cost: float
    reason: str


@dataclass
class Position:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float  # effective fill (after slippage)
    shares: int
    initial_shares: int
    stop: float
    initial_stop: float
    risk_per_share: float
    tp1: float
    tp2: float
    atr: float
    score: float
    regime: str
    sector: str | None
    entry_cost: float
    highest_close: float
    tp1_done: bool = False
    bars_held: int = 0
    pending_exit: str | None = None
    last_close: float = 0.0
    legs: list[ExitLeg] = field(default_factory=list)


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float  # share-weighted average of exit legs
    shares: int
    pnl: float  # net of all costs
    return_pct: float
    r_multiple: float
    holding_days: int
    exit_reason: str
    score: float
    regime: str
    costs: float


@dataclass
class Order:
    candidate: Candidate
    placed: pd.Timestamp
    regime: str


@dataclass
class BacktestResult:
    equity: pd.Series
    daily: pd.DataFrame  # cash, invested, positions, open_risk
    trades: list[Trade]
    stats: dict[str, int]  # order bookkeeping (filled, missed, skipped...)
    settings: Settings

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


class _Bars:
    """Fast date -> OHLC lookup for one symbol."""

    def __init__(self, f: pd.DataFrame) -> None:
        self.pos = {d: i for i, d in enumerate(f.index)}
        self.o = f["open"].to_numpy(float)
        self.h = f["high"].to_numpy(float)
        self.l = f["low"].to_numpy(float)
        self.c = f["close"].to_numpy(float)
        self.atr = f["atr"].to_numpy(float)
        self.ema = f["ema_fast"].to_numpy(float)
        self.macd = f["macd"].to_numpy(float)
        self.sig = f["macd_signal"].to_numpy(float)


class Simulator:
    def __init__(
        self,
        settings: Settings,
        features: dict[str, pd.DataFrame],
        candidates: dict[pd.Timestamp, list[Candidate]],
        regimes: pd.Series,
        calendar: pd.DatetimeIndex,
    ) -> None:
        self.s = settings
        self.bt = settings.backtest
        self.ex = settings.backtest.exits
        self.risk = settings.risk
        self.bars = {sym: _Bars(f) for sym, f in features.items()}
        self.candidates = candidates
        self.regimes = regimes
        self.calendar = calendar
        self.sectors = settings.universe.sectors
        self.fee = self.bt.commission_pct + self.bt.exchange_fee_pct
        self.cash = self.bt.initial_equity
        self.equity = self.bt.initial_equity
        self.positions: dict[str, Position] = {}
        self.orders: list[Order] = []
        self.trades: list[Trade] = []
        self.stats = {"orders": 0, "filled": 0, "missed_limit": 0, "no_bar": 0, "no_capacity": 0}

    # ----------------------------------------------------------------- fills
    def _buy_fill(self, raw: float) -> float:
        return raw * (1 + self.bt.slippage_pct)

    def _sell(self, pos: Position, d: pd.Timestamp, shares: int, raw: float, reason: str) -> None:
        shares = min(shares, pos.shares)
        if shares <= 0:
            return
        price = raw * (1 - self.bt.slippage_pct)
        cost = shares * price * self.fee
        self.cash += shares * price - cost
        pos.shares -= shares
        pos.legs.append(ExitLeg(d, shares, price, cost, reason))
        if pos.shares == 0:
            self._close_trade(pos)

    def _close_trade(self, pos: Position) -> None:
        proceeds = sum(leg.shares * leg.price for leg in pos.legs)
        exit_costs = sum(leg.cost for leg in pos.legs)
        invested = pos.initial_shares * pos.entry_price
        pnl = proceeds - invested - pos.entry_cost - exit_costs
        self.trades.append(
            Trade(
                symbol=pos.symbol,
                entry_date=pos.entry_date,
                exit_date=pos.legs[-1].date,
                entry_price=pos.entry_price,
                exit_price=proceeds / pos.initial_shares,
                shares=pos.initial_shares,
                pnl=pnl,
                return_pct=100 * pnl / invested,
                r_multiple=pnl / (pos.risk_per_share * pos.initial_shares),
                holding_days=pos.bars_held,
                exit_reason=pos.legs[-1].reason,
                score=pos.score,
                regime=pos.regime,
                costs=pos.entry_cost + exit_costs,
            )
        )
        del self.positions[pos.symbol]

    def _stop_reason(self, pos: Position) -> str:
        if pos.stop > pos.initial_stop + 1e-12:
            return (
                "breakeven_stop"
                if pos.tp1_done and pos.stop <= pos.entry_price + 1e-9
                else "trailing_stop"
            )
        return "stop"

    def _take_tp1(self, pos: Position, d: pd.Timestamp, raw: float) -> None:
        qty = math.floor(pos.initial_shares * self.ex.tp1_fraction)
        pos.tp1_done = True
        if qty >= pos.shares:
            self._sell(pos, d, pos.shares, raw, "tp1")
            return
        if qty > 0:
            self._sell(pos, d, qty, raw, "tp1")
        if self.ex.breakeven_after_tp1:
            pos.stop = max(pos.stop, pos.entry_price)

    def _intraday(self, pos: Position, d: pd.Timestamp, h: float, low: float) -> None:
        if low <= pos.stop:
            self._sell(pos, d, pos.shares, pos.stop, self._stop_reason(pos))
            return
        if not pos.tp1_done and h >= pos.tp1:
            self._take_tp1(pos, d, pos.tp1)
            if pos.symbol not in self.positions:
                return
        if pos.tp1_done and h >= pos.tp2:
            self._sell(pos, d, pos.shares, pos.tp2, "tp2")

    def _process_open(self, pos: Position, d: pd.Timestamp, b: _Bars, i: int) -> None:
        o, h, low = b.o[i], b.h[i], b.l[i]
        if pos.pending_exit:
            self._sell(pos, d, pos.shares, o, pos.pending_exit)
            return
        if o <= pos.stop:
            self._sell(pos, d, pos.shares, o, self._stop_reason(pos))
            return
        if not pos.tp1_done and o >= pos.tp1:
            self._take_tp1(pos, d, o)
            if pos.symbol not in self.positions:
                return
        if pos.tp1_done and o >= pos.tp2:
            self._sell(pos, d, pos.shares, o, "tp2")
            return
        self._intraday(pos, d, h, low)

    def _process_close(self, pos: Position, b: _Bars, i: int) -> None:
        c = b.c[i]
        pos.bars_held += 1
        pos.last_close = c
        pos.highest_close = max(pos.highest_close, c)
        if self.ex.trailing_stop and not math.isnan(b.atr[i]):
            pos.stop = trailing_stop(
                pos.highest_close, b.atr[i], self.ex.trailing_atr_multiplier, pos.stop
            )
        if pos.bars_held >= self.ex.max_holding_days:
            pos.pending_exit = "max_hold"
        elif self.ex.trend_exit and c < b.ema[i] and b.macd[i] < b.sig[i]:
            pos.pending_exit = "trend_exit"
        elif (
            pos.bars_held >= self.ex.time_stop_days
            and not pos.tp1_done
            and c < pos.entry_price + self.ex.time_stop_min_r * pos.risk_per_share
        ):
            pos.pending_exit = "time_stop"

    # ----------------------------------------------------------------- entries
    def _open_risk(self) -> float:
        """Capital at risk: entry-to-stop loss still possible on open positions.

        A stop moved to breakeven (or above) frees the position's risk budget.
        """
        return sum(max(0.0, p.entry_price - p.stop) * p.shares for p in self.positions.values())

    def _sector_value(self, sector: str | None) -> float:
        if sector is None:
            return 0.0
        return sum(p.last_close * p.shares for p in self.positions.values() if p.sector == sector)

    def _fill_order(self, order: Order, d: pd.Timestamp) -> None:
        c = order.candidate
        b = self.bars.get(c.symbol)
        i = b.pos.get(d) if b else None
        if i is None:
            self.stats["no_bar"] += 1
            return
        o, low = b.o[i], b.l[i]
        if self.bt.entry_mode == "next_open":
            raw = o
        else:
            limit = c.close + self.risk.entry_zone_atr * c.atr
            if o <= limit:
                raw = o
            elif low <= limit:
                raw = limit
            else:
                self.stats["missed_limit"] += 1
                return
        stop = raw - self.risk.atr_stop_multiplier * c.atr
        entry = self._buy_fill(raw)
        rps = entry - stop
        if stop <= 0 or rps <= 0:
            self.stats["no_capacity"] += 1
            return
        eq = self.equity
        sector = self.sectors.get(c.symbol)
        caps = [
            eq * self.risk.risk_per_trade_pct / 100 / rps,
            eq * self.risk.max_position_pct / 100 / entry,
            self.cash / (entry * (1 + self.fee)),
            (eq * self.risk.max_portfolio_risk_pct / 100 - self._open_risk()) / rps,
        ]
        if sector is not None:
            caps.append(
                (eq * self.risk.max_sector_exposure_pct / 100 - self._sector_value(sector)) / entry
            )
        lot = self.risk.lot_size
        shares = int(math.floor(max(0.0, min(caps)) / lot) * lot)
        if shares <= 0 or shares * entry < eq * self.bt.min_position_pct / 100:
            self.stats["no_capacity"] += 1
            return
        cost = shares * entry * self.fee
        self.cash -= shares * entry + cost
        risk_raw = raw - stop
        pos = Position(
            symbol=c.symbol,
            entry_date=d,
            entry_price=entry,
            shares=shares,
            initial_shares=shares,
            stop=stop,
            initial_stop=stop,
            risk_per_share=rps,
            tp1=raw + self.risk.tp1_r * risk_raw,
            tp2=raw + self.risk.tp2_r * risk_raw,
            atr=c.atr,
            score=c.score,
            regime=order.regime,
            sector=sector,
            entry_cost=cost,
            highest_close=raw,
            last_close=raw,
        )
        self.positions[c.symbol] = pos
        self.stats["filled"] += 1
        self._intraday(pos, d, b.h[i], b.l[i])

    # ----------------------------------------------------------------- loop
    def run(self) -> BacktestResult:
        records = []
        daily_rate = (1 + self.bt.cash_interest_annual_pct / 100) ** (1 / 252) - 1
        for d in self.calendar:
            if daily_rate and self.cash > 0:
                self.cash += self.cash * daily_rate
            for sym in list(self.positions):
                b = self.bars[sym]
                i = b.pos.get(d)
                if i is not None:
                    self._process_open(self.positions[sym], d, b, i)

            orders, self.orders = self.orders, []
            for order in orders:
                if order.candidate.symbol not in self.positions:
                    self._fill_order(order, d)

            for sym, pos in list(self.positions.items()):
                b = self.bars[sym]
                i = b.pos.get(d)
                if i is not None:
                    self._process_close(pos, b, i)

            invested = sum(p.last_close * p.shares for p in self.positions.values())
            self.equity = self.cash + invested
            records.append(
                {
                    "date": d,
                    "equity": self.equity,
                    "cash": self.cash,
                    "invested": invested,
                    "positions": len(self.positions),
                    "open_risk": self._open_risk(),
                }
            )

            free = self.risk.max_open_positions - len(self.positions)
            regime = self.regimes.get(d, "")
            for cand in self.candidates.get(d, []):
                if free <= 0:
                    break
                if cand.symbol in self.positions:
                    continue
                self.orders.append(Order(cand, d, regime))
                self.stats["orders"] += 1
                free -= 1

        if len(self.calendar):
            last = self.calendar[-1]
            for pos in list(self.positions.values()):
                self._sell(pos, last, pos.shares, pos.last_close, "end_of_test")
            records[-1]["equity"] = self.cash
            records[-1]["cash"] = self.cash
            records[-1]["invested"] = 0.0
        daily = (
            pd.DataFrame(records).set_index("date")
            if records
            else pd.DataFrame(columns=["equity", "cash", "invested", "positions", "open_risk"])
        )
        return BacktestResult(daily["equity"], daily, self.trades, self.stats, self.s)


def run_backtest(
    settings: Settings,
    features: dict[str, pd.DataFrame],
    candidates: dict[pd.Timestamp, list[Candidate]],
    regimes: pd.Series,
    calendar: pd.DatetimeIndex,
) -> BacktestResult:
    return Simulator(settings, features, candidates, regimes, calendar).run()


__all__ = ["BacktestResult", "Trade", "run_backtest"]
