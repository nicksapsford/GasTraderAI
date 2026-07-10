"""
GasTrader AI -- strategy_gas.py
natural gas spread betting strategy mechanics.
Points-based trailing stop. Prices are in USD per MMBtu; P&L is computed
in USD then converted to GBP using the live GBPUSD rate from Capital.com.

Sizing (confirmed/approved settings):
  Stop distance:   60 points ($60/MMBtu)   (widened for Natural Gas volatility)
  Take profit:     300 points ($300/MMBtu) safety ceiling
  Max risk/trade:  £20  (2% of £1,000)
  => stake sized so a full stop-out risks ~£20  (varies slightly with GBPUSD)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("GasTrader.Strategy")

# ── Settings ──────────────────────────────────────────────────────────────────

# Natural Gas is highly volatile, especially around EIA Gas Storage reports (Thu 14:30 UTC). Stops and targets widened to accommodate typical NATURALGAS intraday ranges.
TRAILING_STOP_POINTS   = 60.0    # trailing stop in gas points ($/MMBtu)
TAKE_PROFIT_POINTS     = 300.0   # safety ceiling (trailing stop exits first in practice)
MAX_RISK_PER_TRADE_GBP = 20.0    # max GBP loss per trade (2% of £1,000)
SPREAD_POINTS          = 0.3     # Capital.com gas spread (very low cost)
DEFAULT_GBPUSD         = 1.27    # conservative fallback if live rate unavailable

# Force close 15 minutes before the 21:00 UTC daily close -- NEVER hold overnight.
FORCE_CLOSE_START_MIN  = 20 * 60 + 45   # 20:45 UTC
DAILY_CLOSE_MIN        = 21 * 60        # 21:00 UTC


# ── GBPUSD conversion ─────────────────────────────────────────────────────────

_last_gbpusd = DEFAULT_GBPUSD


def get_gbpusd_rate(connector=None) -> float:
    """
    Return the live GBPUSD rate from Capital.com (epic GBPUSD).
    NB: the connector rounds 'mid' to 1 dp (fine for gas, useless for FX), so we
    compute the mid ourselves from the raw bid/ask. Falls back to the last good
    rate, then DEFAULT_GBPUSD, if the market is unavailable.
    """
    global _last_gbpusd
    if connector is not None:
        try:
            if getattr(connector, "connected", False):
                p = connector.get_price("GBPUSD")
                if p and p.get("bid") and p.get("ask"):
                    rate = (float(p["bid"]) + float(p["ask"])) / 2
                    if rate > 0:
                        _last_gbpusd = round(rate, 5)
        except Exception as exc:
            log.warning("GBPUSD fetch failed: %s -- using %.4f", exc, _last_gbpusd)
    return _last_gbpusd


# ── Sizing helpers ────────────────────────────────────────────────────────────

def calculate_size(stop_pts: float = TRAILING_STOP_POINTS,
                   gbpusd: float = DEFAULT_GBPUSD,
                   risk_gbp: float = MAX_RISK_PER_TRADE_GBP) -> float:
    """
    Position size in MMBtu so that a full stop-out loses ~risk_gbp.
      risk_gbp = stop_pts * size_oz / gbpusd   =>   size_oz = risk_gbp * gbpusd / stop_pts
    At GBPUSD 1.3376 this is ~0.89 oz for a 30pt stop and £20 risk.
    """
    if stop_pts <= 0:
        return 0.0
    return round(risk_gbp * gbpusd / stop_pts, 2)


def calculate_stake(stop_pts: float = TRAILING_STOP_POINTS,
                    gbpusd: float = DEFAULT_GBPUSD) -> float:
    """Return £ risk per point for the given stop distance (= size_oz / gbpusd)."""
    size_oz = calculate_size(stop_pts, gbpusd)
    return round(size_oz / gbpusd, 4) if gbpusd > 0 else 0.0


def calculate_entry(current_price: float, direction: str) -> float:
    """Entry price for a market order (mid). Direction kept for interface parity."""
    return round(float(current_price), 2)


def calculate_stop_loss(entry_price: float, direction: str,
                        stop_pts: float = TRAILING_STOP_POINTS) -> float:
    return round(entry_price - stop_pts if direction == "LONG" else entry_price + stop_pts, 2)


def calculate_take_profit(entry_price: float, direction: str,
                          tp_pts: float = TAKE_PROFIT_POINTS) -> float:
    return round(entry_price + tp_pts if direction == "LONG" else entry_price - tp_pts, 2)


def calculate_pnl(entry_price: float, exit_price: float, direction: str,
                  size_oz: float, gbpusd: float) -> tuple:
    """
    Return (points_gained, pnl_usd, pnl_gbp), net of spread.
      points_gained = price move in USD points (minus spread)
      pnl_usd       = points_gained * size_oz  ($1/MMBtu per point)
      pnl_gbp       = pnl_usd / gbpusd
    """
    raw_pts = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
    points  = raw_pts - SPREAD_POINTS
    pnl_usd = points * size_oz
    pnl_gbp = pnl_usd / gbpusd if gbpusd > 0 else 0.0
    return round(points, 2), round(pnl_usd, 2), round(pnl_gbp, 2)


def should_force_close(ts_utc: Optional[datetime] = None) -> bool:
    """True during the 20:45-21:00 UTC pre-close window -- force close all positions."""
    if ts_utc is None:
        ts_utc = datetime.now(timezone.utc)
    hm = ts_utc.hour * 60 + ts_utc.minute
    return FORCE_CLOSE_START_MIN <= hm < DAILY_CLOSE_MIN


# ── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class GasTrade:
    """
    A single Gas spread bet. Entry/exit prices are USD per MMBtu.
    P&L is computed in USD then converted to GBP at the exit-time GBPUSD rate.
    """
    direction:        str
    entry_price:      float
    stop_pts:         float = TRAILING_STOP_POINTS
    size_oz:          float = 0.0
    gbpusd_entry:     float = DEFAULT_GBPUSD
    entry_time:       object = field(default=None)
    liquidity_period: str    = field(default="")

    def __post_init__(self):
        if self.size_oz <= 0:
            self.size_oz = calculate_size(self.stop_pts, self.gbpusd_entry)
        self.stake        = round(self.size_oz / self.gbpusd_entry, 4) if self.gbpusd_entry > 0 else 0.0
        self.trail_best   = self.entry_price
        self.stop_loss    = calculate_stop_loss(self.entry_price, self.direction, self.stop_pts)
        self.take_profit  = calculate_take_profit(self.entry_price, self.direction)
        self.exit_price   = None
        self.exit_time    = None
        self.exit_reason  = None
        self.points_gained = None
        self.pnl_usd      = None
        self.pnl_gbp      = None
        self.gbpusd_exit  = None
        if self.entry_time is None:
            self.entry_time = datetime.now(timezone.utc)

    def update_trailing_stop(self, price: float) -> bool:
        """Move the stop in favour of the trade. Returns True if moved."""
        if self.direction == "LONG" and price > self.trail_best:
            self.trail_best = price
            new_sl = price - self.stop_pts
            if new_sl > self.stop_loss:
                self.stop_loss = round(new_sl, 2)
                log.info("  Trailing stop moved UP to %.2f (price=%.2f)", self.stop_loss, price)
                return True
        elif self.direction == "SHORT" and price < self.trail_best:
            self.trail_best = price
            new_sl = price + self.stop_pts
            if new_sl < self.stop_loss:
                self.stop_loss = round(new_sl, 2)
                log.info("  Trailing stop moved DOWN to %.2f (price=%.2f)", self.stop_loss, price)
                return True
        return False

    def check_exit(self, price: float) -> Optional[str]:
        """Check stop loss and take profit. Returns exit reason or None."""
        if self.direction == "LONG":
            if price <= self.stop_loss:   return "STOP_LOSS"
            if price >= self.take_profit: return "TAKE_PROFIT"
        else:
            if price >= self.stop_loss:   return "STOP_LOSS"
            if price <= self.take_profit: return "TAKE_PROFIT"
        return None

    def close(self, price: float, reason: str, gbpusd: float) -> None:
        """Record exit and compute USD + GBP P&L at the given GBPUSD rate."""
        self.exit_price   = round(price, 2)
        self.exit_time    = datetime.now(timezone.utc)
        self.exit_reason  = reason
        self.gbpusd_exit  = round(gbpusd, 5)
        pts, pnl_usd, pnl_gbp = calculate_pnl(
            self.entry_price, price, self.direction, self.size_oz, gbpusd,
        )
        self.points_gained = pts
        self.pnl_usd       = pnl_usd
        self.pnl_gbp       = pnl_gbp

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    def summary(self) -> str:
        if self.is_open:
            return (
                f"[OPEN {self.direction}] entry=${self.entry_price:,.2f} "
                f"stop=${self.stop_loss:,.2f} target=${self.take_profit:,.2f} "
                f"size={self.size_oz:.2f}oz stake=£{self.stake:.4f}/pt"
            )
        sign = "WIN " if (self.pnl_gbp or 0) >= 0 else "LOSS"
        return (
            f"[{sign} {self.direction}] entry=${self.entry_price:,.2f} "
            f"exit=${self.exit_price:,.2f} pts={self.points_gained:+.1f} "
            f"P&L=£{self.pnl_gbp:+.2f} (${self.pnl_usd:+.2f} @ {self.gbpusd_exit}) "
            f"reason={self.exit_reason}"
        )


# ── Open / close helpers ──────────────────────────────────────────────────────

def open_trade(direction: str, price: float, gbpusd: float,
               liquidity_period: str = "", stop_pts: float = TRAILING_STOP_POINTS) -> GasTrade:
    trade = GasTrade(
        direction        = direction,
        entry_price      = round(price, 2),
        stop_pts         = stop_pts,
        gbpusd_entry     = gbpusd,
        entry_time       = datetime.now(timezone.utc),
        liquidity_period = liquidity_period,
    )
    log.info(
        ">>> TRADE OPENED | %s | entry=$%.2f | size=%.2foz | stake=£%.4f/pt | "
        "stop=$%.2f | target=$%.2f | %s | GBPUSD=%.4f",
        direction, price, trade.size_oz, trade.stake,
        trade.stop_loss, trade.take_profit, liquidity_period, gbpusd,
    )
    return trade


def close_trade(trade: GasTrade, price: float, reason: str, gbpusd: float) -> GasTrade:
    trade.close(price, reason, gbpusd)
    sign = "WIN " if trade.pnl_gbp >= 0 else "LOSS"
    log.info(
        "<<< TRADE CLOSED | [%s %s] | pts=%+.1f | P&L=£%+.2f ($%+.2f) | reason=%s",
        sign, trade.direction, trade.points_gained, trade.pnl_gbp, trade.pnl_usd, reason,
    )
    return trade


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Strategy self-test (Gas)")
    gbpusd = 1.3376
    t = open_trade("LONG", 4155.63, gbpusd, "OVERLAP")
    log.info("%s", t.summary())
    log.info("Size=%.2foz | stake=£%.4f/pt | max risk=£%.2f",
             t.size_oz, t.stake, t.stop_pts * t.size_oz / gbpusd)
    t.update_trailing_stop(4185.0)
    log.info("After +30pt move: stop=$%.2f", t.stop_loss)
    reason = t.check_exit(4150.0)
    log.info("Check exit at 4150: %s", reason)
    close_trade(t, 4150.0, "STOP_LOSS", gbpusd)
    log.info("%s", t.summary())
    log.info("Force close at 20:45 UTC now? %s", should_force_close())
