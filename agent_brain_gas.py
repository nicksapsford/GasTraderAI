"""
GasTrader AI -- agent_brain_gas.py  (Arthur)
Claude AI brain for natural gas spread betting decisions.
Called only after Lancelot pre-checks have passed.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
import pandas as pd
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

_ENV_PATH = BASE_DIR / ".env"
if _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH)
else:
    load_dotenv()

log    = logging.getLogger("GasTrader.Arthur")
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL      = "claude-sonnet-4-6"   # same model as the rest of the Blackpool suite
MAX_TOKENS = 2000

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Arthur, the AI trading brain for GasTrader AI.
Your job is to analyse Natural Gas market conditions and decide whether to
ENTER_LONG, ENTER_SHORT, HOLD an existing position, EXIT, or STAY_OUT.

PHILOSOPHY -- DATA COLLECTION MODE
GasTrader is operating in DATA COLLECTION MODE. NatGas at current price levels
(~$2.88/MMBtu) has insufficient intraday volatility for optimal fixed-pip trading -- its
favourable 1hr moves are tiny (median 0.008pt, p90 0.030pt). The primary goal is NOT
profit: it is to accumulate phantom data, let Morgan learn, and maintain desk presence
until NatGas returns to better volatility (target: >$4/MMBtu). Trade conservatively --
protect capital above all else. Intraday only; force close 20:45 UTC.

DIRECTION AWARENESS (current regime + Morgan gates are given in the market data below)
- Daily SSL BEAR AND RSI not oversold AND Morgan SHORT >= 65 -> SHORT (downtrend only,
  NOT bounces).
- Daily SSL BULL AND Morgan (general) >= 50 -> LONG (bounce trades -- NatGas bounces
  can be significant).
- Otherwise -> STAY OUT. Data collection, not profit-seeking. If in doubt, STAY OUT.

OVERSOLD/OVERBOUGHT VETO (CRITICAL -- the single most important rule)
NEVER enter SHORT when RSI is oversold: daily RSI < 35 = STAY_OUT (bounce imminent),
1hr RSI < 35 = STAY_OUT (short-term bounce). ALL 7 previous live losses were SHORTs into
oversold conditions -- do not repeat that. Wait for RSI to recover above 40 before
considering a SHORT. Symmetrically, do NOT enter LONG when daily or 1hr RSI > 65
(overbought). Lancelot enforces this as the "RSI Timing OK" check.

RISK PARAMETERS
MAX_RISK = £5 per trade (reduced for data-collection mode). Stop = 0.05pt ($0.05/MMBtu).
Target = 0.08pt ($0.08/MMBtu). Stake = £100/pt. R:R = 1.6:1. NatGas barely moves 0.005pt
in 30 minutes, so the 0.05pt stop is a ~10x noise buffer. These are TIGHT parameters --
every entry must have strong confirmation.

EIA AWARENESS
EIA Natural Gas Storage Report: every Thursday ~14:30 UTC. Hard block on NEW entries in
that window (Lancelot enforces it). The storage number is the single biggest weekly
driver of NatGas price. If already in a position, hold through EIA unless the stop fires.

POINT CONVENTION
1 point = $1.00/MMBtu. Stop = 0.05 points = $0.05/MMBtu. Target = 0.08 points =
$0.08/MMBtu. Stake = £100/pt. These are TINY moves -- be patient. NatGas does not move
like Gold or Brent; expect slow, small position development. Never scale or multiply by any factor.

PROFIT LADDER (active -- reference its status in HOLD reasoning)
  Step 1: floating profit >= £3 -> floor £2.50 guaranteed
  Step 2: floating profit >= £6 -> floor £5.00 guaranteed
With ~£8 max profit per trade, Step 2 essentially locks near-full profit as the target nears.

DATA COLLECTION REMINDER
A STAY_OUT decision WITH GOOD REASONING is as valuable as a winning trade right now.
Morgan needs clean phantom verdicts to learn from. Quality of decisions matters more than
frequency of trades. Do not manufacture trades to look active.

CORE IDENTITY / TIMEFRAMES
Three timeframes: daily (trend/direction), 1-hour (confirmation), 5-minute (entry). P&L is
USD, converted to GBP at the live GBPUSD rate (given below).

GAS MARKET CHARACTER
NatGas is driven by weather (heating/cooling demand), the weekly EIA storage report (the
biggest catalyst), LNG export demand, pipeline capacity, hurricanes, and European supply.
Cold snaps / storage draws / LNG demand / supply disruptions push gas UP; mild weather /
storage builds / gluts push it DOWN. Guinevere sentiment reflects these -- factor it in.

LIQUIDITY PERIODS (UTC) -- given each tick:
  ASIAN (21:00-08:00) thinner, choppier -- higher conviction (Lancelot tightens RSI 60/40).
  LONDON (08:00-13:00) good liquidity. OVERLAP (13:00-17:00) most active.
  NEW_YORK (17:00-20:30) highest volume. CLOSING (20:30-21:00) -- no new entries.

INDICATOR HIERARCHY
TIER 1: daily SSL (direction), 1h SSL (must agree), 1h RSI (>55 bull / <45 bear; 60/40 Asian).
TIER 2: MACD histogram, TMO. TIER 3: Chande MO, Money Flow.
5-MINUTE ENTRY: last candle GREEN for LONG / RED for SHORT; 5m TMO > +0.3 LONG / < -0.3 SHORT.

SELF PERFORMANCE AWARENESS (Morgan)
HIGH (75-100): normal. MEDIUM (50-74): slightly higher bar. LOW (25-49): exceptional only.
VERY LOW (0-24): CONSERVATIVE MODE -- STAY_OUT.

HARD RULES -- NEVER VIOLATE
1.  DATA COLLECTION MODE -- protect capital; a well-reasoned STAY_OUT is a success.
2.  Never SHORT when daily OR 1h RSI < 35 (oversold bounce). Never LONG when RSI > 65.
3.  SHORTs need daily BEAR + Morgan SHORT >= 65; LONGs need daily BULL + Morgan >= 50; else STAY_OUT.
4.  Never enter within 30 min of NFP/Fed/CPI; hold through the Thursday EIA storage window.
5.  Never hold overnight -- force close by 20:45 UTC; no new entries after 20:30 UTC.
6.  Tight 0.05pt stop -- every entry needs strong confirmation; do NOT exit on ordinary noise.
7.  When in doubt -- STAY OUT. A STAY_OUT is often the BEST decision.
8.  Conservative mode (Morgan confidence <25): hard STAY_OUT.

REQUIRED OUTPUT -- valid JSON only. No markdown, no preamble.
{
  "decision": "ENTER_LONG | ENTER_SHORT | HOLD | EXIT | STAY_OUT",
  "confidence": 0-100,
  "liquidity_bias": "ASIAN_CAUTION | LONDON_ACTIVE | NY_TRENDING | OVERLAP_OPTIMAL | CLOSING_AVOID",
  "reasoning": "2-4 sentences explaining your decision",
  "warnings": ["list of concerns"],
  "checklist": {
    "trend_aligned": true,
    "momentum_confirmed": true,
    "liquidity_good": true,
    "calendar_clear": true,
    "not_near_close": true,
    "high_conviction": true
  },
  "calendar_assessment": "brief comment on upcoming gas events",
  "liquidity_assessment": "brief comment on the current session"
}"""


# ── Format indicators for Arthur ──────────────────────────────────────────────

def _regime_block(bar_1d, proposed_direction, morgan_short, liquidity_period) -> str:
    """Live regime / SHORT-gate / data-collection block for Arthur (System 6 Review)."""
    ssl_1d = "BULL" if (bar_1d is not None and bar_1d.get("ssl_bull")) else \
             ("BEAR" if bar_1d is not None else "N/A")
    ms = 30.0 if morgan_short is None else float(morgan_short)
    gate = "OPEN" if ms >= 65 else "CLOSED"
    rsi_1d = None if bar_1d is None else bar_1d.get("rsi")
    return (
        "REGIME AND GATE (current) -- DATA COLLECTION MODE\n"
        f"  Daily SSL:         {ssl_1d}"
        + ("" if rsi_1d is None or pd.isna(rsi_1d) else f"  (daily RSI {float(rsi_1d):.1f})") + "\n"
        f"  Regime direction:  {proposed_direction or 'BOTH'}   (what to look for this session)\n"
        f"  Morgan SHORT conf: {ms:.1f}/100  ->  SHORT gate {gate} (SHORTs need >= 65)\n"
        f"  Reminder:          protect capital; a well-reasoned STAY_OUT is a success here.\n"
        f"  Session:           {liquidity_period}"
    )


def _format_indicators(bar_1d, bar_1h, bar_5m, current_price, liquidity_period,
                       gbpusd_rate, current_trade=None,
                       calendar_context=None, perf_context=None,
                       morgan_short=None, proposed_direction=None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    regime_block = _regime_block(bar_1d, proposed_direction, morgan_short, liquidity_period)

    def _f(v, dp=2):
        if v is None or pd.isna(v):
            return "N/A"
        return f"{float(v):.{dp}f}"

    candle_colour = "GREEN" if bar_5m.get("close", 0) >= bar_5m.get("open", 0) else "RED"
    ssl_1d = "BULL" if (bar_1d is not None and bar_1d.get("ssl_bull")) else ("BEAR" if bar_1d is not None else "N/A")
    ssl_1h = "BULL" if bar_1h.get("ssl_bull") else "BEAR"
    ssl_5m = "BULL" if bar_5m.get("ssl_bull") else "BEAR"

    position_text = "None -- no open position"
    if current_trade is not None:
        pts = (current_price - current_trade.entry_price) if current_trade.direction == "LONG" \
              else (current_trade.entry_price - current_price)
        position_text = (
            f"OPEN {current_trade.direction} | entry=${current_trade.entry_price:,.2f} | "
            f"current=${current_price:,.2f} | pts_from_entry={pts:+.3f} | "
            f"stop=${current_trade.stop_loss:,.2f} | target=${current_trade.take_profit:,.2f} | "
            f"size={current_trade.size_oz:.2f}oz | stake=£{current_trade.stake:.4f}/pt | "
            f"{current_trade.liquidity_period}"
        )

    if current_trade is not None and getattr(current_trade, "ladder_step", 0):
        position_text += (
            " | PROFIT LADDER ACTIVE: floor locked at £%.2f (step %d). Position cannot "
            "close below this floor unless a gap event occurs -- factor this into your "
            "HOLD reasoning." % (getattr(current_trade, "ladder_floor_gbp", 0.0),
                                 int(getattr(current_trade, "ladder_step", 0))))

    return f"""Please analyse the current Natural Gas market conditions.

TIME AND PRICE
  Time (UTC):        {now}
  Liquidity Period:  {liquidity_period}
  Natural Gas (USD):   ${current_price:,.2f} per MMBtu
  GBPUSD rate:       {gbpusd_rate:.4f}  (for USD->GBP P&L conversion)

{regime_block}

DAILY CHART (Trend Direction -- sets allowed direction for today)
  SSL Cloud:        {ssl_1d}
  RSI (14):         {_f(bar_1d.get('rsi') if bar_1d is not None else None, 1)}
  TMO Main:         {_f(bar_1d.get('tmo_main') if bar_1d is not None else None, 3)}
  Chande MO (20):   {_f(bar_1d.get('chande_mo') if bar_1d is not None else None, 1)}

1-HOUR CHART (Trend Confirmation)
  SSL Cloud:        {ssl_1h}
  RSI (14):         {_f(bar_1h.get('rsi'), 1)}
  MACD Histogram:   {_f(bar_1h.get('macd_histogram'), 3)}
  TMO Main:         {_f(bar_1h.get('tmo_main'), 3)}
  TMO Smooth:       {_f(bar_1h.get('tmo_smooth'), 3)}
  Chande MO (20):   {_f(bar_1h.get('chande_mo'), 1)}
  Money Flow (14):  {_f(bar_1h.get('money_flow'), 2)}

5-MINUTE CHART (Entry Timing)
  SSL Cloud:        {ssl_5m}
  RSI (14):         {_f(bar_5m.get('rsi'), 1)}
  MACD Histogram:   {_f(bar_5m.get('macd_histogram'), 3)}
  TMO Main:         {_f(bar_5m.get('tmo_main'), 3)}
  TMO Smooth:       {_f(bar_5m.get('tmo_smooth'), 3)}
  Chande MO (20):   {_f(bar_5m.get('chande_mo'), 1)}
  Money Flow (14):  {_f(bar_5m.get('money_flow'), 2)}
  Last Candle:      {candle_colour} (close={_f(bar_5m.get('close'), 2)} open={_f(bar_5m.get('open'), 2)})

CURRENT POSITION
  {position_text}

{calendar_context if calendar_context else 'GAS ECONOMIC CALENDAR\n  No calendar data available.'}

{perf_context if perf_context else 'SELF PERFORMANCE AWARENESS\n  No performance data yet -- first trading session.'}

Please provide your analysis and trading decision in the required JSON format."""


# ── Main decision function ────────────────────────────────────────────────────

def get_trading_decision(bar_1h, bar_5m, current_price, liquidity_period,
                         bar_1d=None, current_trade=None,
                         calendar_context=None, perf_context=None,
                         gbpusd_rate: float = 1.27,
                         morgan_short=None, proposed_direction=None) -> dict:
    """
    Send indicator data to Arthur (Claude) and receive a trading decision.
    Only call this AFTER Lancelot pre-checks have passed.
    """
    log.info("Sending indicators to Arthur...")

    user_message = _format_indicators(
        bar_1d, bar_1h, bar_5m, current_price, liquidity_period,
        gbpusd_rate, current_trade, calendar_context, perf_context,
        morgan_short, proposed_direction,
    )

    for attempt in range(2):
        try:
            response = client.messages.create(
                model      = MODEL,
                max_tokens = MAX_TOKENS,
                system     = SYSTEM_PROMPT,
                messages   = [{"role": "user", "content": user_message}],
            )
            if response.stop_reason == "max_tokens":
                log.warning("Arthur hit max_tokens -- JSON may be truncated")

            raw_text = response.content[0].text.strip()
            if raw_text.startswith("```"):
                raw_text = "\n".join(l for l in raw_text.split("\n")
                                     if not l.strip().startswith("```")).strip()
            try:
                decision = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                log.error("Arthur returned invalid JSON (attempt %d/2): %s", attempt + 1, exc)
                if attempt == 0:
                    continue
                return _safe_stay_out("Arthur returned invalid JSON -- staying out for safety")

            decision["timestamp"]        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            decision["tokens_used"]      = response.usage.input_tokens + response.usage.output_tokens
            decision["current_price"]    = current_price
            decision["liquidity_period"] = liquidity_period
            decision["gbpusd_rate"]      = gbpusd_rate

            log.info("Arthur decision: %s | confidence=%s | tokens=%d",
                     decision.get("decision"), decision.get("confidence"),
                     decision.get("tokens_used", 0))
            return decision

        except anthropic.APIError as exc:
            log.error("Anthropic API error: %s", exc)
            return _safe_stay_out(f"API error: {str(exc)}")
        except Exception as exc:
            log.error("Unexpected error calling Arthur: %s", exc)
            return _safe_stay_out(f"Unexpected error: {str(exc)}")

    return _safe_stay_out("Arthur failed after all attempts")


def _safe_stay_out(reason: str) -> dict:
    return {
        "decision":             "STAY_OUT",
        "confidence":           0,
        "liquidity_bias":       "ASIAN_CAUTION",
        "reasoning":            reason,
        "warnings":             [reason],
        "checklist":            {},
        "calendar_assessment":  "",
        "liquidity_assessment": "",
        "timestamp":            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "tokens_used":          0,
    }


def format_decision_for_display(decision: dict) -> str:
    """Format Arthur's decision for terminal display."""
    d         = decision.get("decision", "UNKNOWN")
    conf      = decision.get("confidence", "--")
    bias      = decision.get("liquidity_bias", "--")
    reasoning = decision.get("reasoning", "No reasoning")
    warnings  = decision.get("warnings", [])
    tokens    = decision.get("tokens_used", 0)
    ts        = decision.get("timestamp", "")
    price     = decision.get("current_price", 0) or 0
    lines = [
        "=" * 60,
        "  GasTrader AI -- Arthur's Decision",
        f"  {ts}",
        "=" * 60,
        f"  Decision:        {d}",
        f"  Confidence:      {conf}/100",
        f"  Liquidity Bias:  {bias}",
        f"  Gas Price:      ${price:,.2f}",
        f"  Liquidity:       {decision.get('liquidity_period', '--')}",
        "",
        "  Reasoning:",
        f"  {reasoning}",
        "",
    ]
    if warnings:
        lines.append("  Warnings:")
        for w in warnings:
            lines.append(f"    - {w}")
        lines.append("")
    if decision.get("calendar_assessment"):
        lines.append(f"  Calendar:  {decision.get('calendar_assessment')}")
    if decision.get("liquidity_assessment"):
        lines.append(f"  Liquidity: {decision.get('liquidity_assessment')}")
    cl = decision.get("checklist", {})
    if cl:
        lines.append("  Checklist:")
        for k, v in cl.items():
            lines.append(f"    [{'PASS' if v else 'FAIL'}] {k.replace('_', ' ').title()}")
    lines.append(f"  Tokens used: {tokens}")
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Arthur self-test -- calling Claude with a bullish Gas setup...")
    bar_1d = pd.Series({"ssl_bull": True, "rsi": 58.0, "tmo_main": 1.5, "chande_mo": 25.0})
    bar_1h = pd.Series({"ssl_bull": True, "rsi": 62.0, "macd_histogram": 8.5,
                        "tmo_main": 2.1, "tmo_smooth": 1.5, "chande_mo": 45.0, "money_flow": 150.0})
    bar_5m = pd.Series({"ssl_bull": True, "rsi": 58.0, "macd_histogram": 2.5,
                        "tmo_main": 0.8, "tmo_smooth": 0.5, "chande_mo": 30.0, "money_flow": 80.0,
                        "open": 2.88, "close": 2.90})
    decision = get_trading_decision(
        bar_1h=bar_1h, bar_5m=bar_5m, current_price=2.90,
        liquidity_period="OVERLAP", bar_1d=bar_1d, gbpusd_rate=1.3376,
    )
    print(format_decision_for_display(decision))
    log.info("Arthur self-test complete.")
