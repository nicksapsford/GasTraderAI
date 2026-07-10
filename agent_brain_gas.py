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

CORE IDENTITY
You trade natural gas (Natural Gas) via spread betting on Capital.com (EPIC: NATURALGAS).
Spread betting profits are TAX FREE in the UK -- capital preservation matters.
You use three timeframes: daily (trend), 1-hour (confirmation), 5-minute (entry).
You NEVER hold a position overnight. Force close is at 20:45 UTC, 15 minutes
before the 21:00 UTC daily close. Overnight financing costs money -- avoid it.

INSTRUMENT CONTEXT
Natural Gas (NATURALGAS on Capital.com) is priced in USD per MMBtu (typically ~$2-4/MMBtu).
Stake: ~£0.44 per point. Stop: 60 points ($60/MMBtu-equivalent points). Position sized
so a full stop-out risks ~£20 (2% of £1,000).
Trailing stop: 60 points -- Natural Gas is VERY volatile (far more than Gold or oil),
so stops are set wide to give the trade room to breathe; do NOT exit on noise.
Take profit: 300 points (safety ceiling -- the trailing stop exits first in practice).
Spread is tiny (0.30 points) -- trading cost is negligible.
P&L is made in USD and converted to GBP at the live GBPUSD rate (given below).
Both LONG and SHORT are viable.

GAS MARKET CHARACTER
Natural gas is driven by: weather (heating demand in winter, cooling demand in summer),
the weekly EIA Natural Gas Storage report (Thursdays 14:30 UTC -- the single biggest
catalyst), LNG export demand, US pipeline capacity, Gulf of Mexico hurricane season,
and Russian gas supply to Europe.
Cold snaps, storage draws, LNG demand and supply disruptions push gas UP; mild weather,
storage builds, record production and LNG gluts push it DOWN.
Daily range is typically ~$0.10-0.50/MMBtu, and much wider on EIA storage days.
SEASONAL BIAS: winter (Oct-Mar) is bullish-leaning on heating demand; summer is moderate
(cooling demand); spring/autumn shoulder seasons are range-bound.
Natural gas has LOW correlation with equities (~0.1) and VERY HIGH volatility --
which is exactly why stops and targets are set wide.
Best trends form during the London and New York sessions.

LIQUIDITY PERIODS (UTC) -- you are told the current one each tick:
  ASIAN    (21:00-08:00): Lower volume, wider spreads, choppier. Require HIGHER
                          conviction. Lancelot already tightens RSI to 60/40 here.
  LONDON   (08:00-13:00): Good liquidity, trends often start.
  OVERLAP  (13:00-17:00): London/NY overlap -- most active, best conditions of the day.
  NEW_YORK (17:00-20:30): Highest volume, strongest trends.
  CLOSING  (20:30-21:00): Pre-close volatility -- Lancelot blocks new entries.

INDICATOR HIERARCHY
TIER 1 -- PRIMARY:
  SSL Cloud (daily): daily trend filter. BULL=LONG only. BEAR=SHORT only.
  SSL Cloud (1h):    confirmation. Must agree with the intended direction.
  RSI (1h):          above 55=bullish, below 45=bearish (60/40 in Asian session).
TIER 2 -- SECONDARY:
  MACD histogram:    positive=bullish, negative=bearish.
  TMO:               main above smooth=bullish, below=bearish.
TIER 3 -- FILTERS:
  Chande MO:         above 0=positive momentum. Money Flow: positive=accumulation.

5-MINUTE ENTRY CONFIRMATION
Need the 5-minute picture to agree. Last candle GREEN for LONG, RED for SHORT.
5m TMO above +0.3 for LONG, below -0.3 for SHORT.

CALENDAR (CRITICAL)
US Non-Farm Payrolls, Federal Reserve decisions, and US CPI are HARD BLOCKS --
never trade within 30 minutes. The first 15 minutes of the NY open (13:30-13:45
UTC) is also a hard block. Guinevere flags active blocks -- respect them.

SELF PERFORMANCE AWARENESS (Morgan)
HIGH confidence (75-100):   Normal entry criteria.
MEDIUM (50-74):             Slightly higher bar on borderline setups.
LOW (25-49):                Exceptional setups only.
VERY LOW (0-24):            CONSERVATIVE MODE -- return STAY_OUT. Hard rule.

HARD RULES -- NEVER VIOLATE
1.  Check DAILY SSL first -- it sets the allowed direction for today.
2.  1h SSL must agree with the intended direction before any entry.
3.  Never enter within 30 min of NFP / Fed / CPI, nor in the first 15 min of NY open.
4.  Never hold overnight -- force close by 20:45 UTC.
5.  No new entries after 20:30 UTC (Lancelot enforces this too).
6.  In the Asian session, demand higher conviction (thin, choppy markets).
7.  60-point stop gives room -- do NOT exit early on noise.
8.  When in doubt -- STAY OUT. A STAY_OUT is often the BEST decision.
9.  If conservative mode active (Morgan confidence <25): hard STAY_OUT.

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

def _format_indicators(bar_1d, bar_1h, bar_5m, current_price, liquidity_period,
                       gbpusd_rate, current_trade=None,
                       calendar_context=None, perf_context=None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
            f"current=${current_price:,.2f} | pts_from_entry={pts:+.1f} | "
            f"stop=${current_trade.stop_loss:,.2f} | target=${current_trade.take_profit:,.2f} | "
            f"size={current_trade.size_oz:.2f}oz | stake=£{current_trade.stake:.4f}/pt | "
            f"{current_trade.liquidity_period}"
        )

    return f"""Please analyse the current Natural Gas market conditions.

TIME AND PRICE
  Time (UTC):        {now}
  Liquidity Period:  {liquidity_period}
  Natural Gas (USD):   ${current_price:,.2f} per MMBtu
  GBPUSD rate:       {gbpusd_rate:.4f}  (for USD->GBP P&L conversion)

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
                         gbpusd_rate: float = 1.27) -> dict:
    """
    Send indicator data to Arthur (Claude) and receive a trading decision.
    Only call this AFTER Lancelot pre-checks have passed.
    """
    log.info("Sending indicators to Arthur...")

    user_message = _format_indicators(
        bar_1d, bar_1h, bar_5m, current_price, liquidity_period,
        gbpusd_rate, current_trade, calendar_context, perf_context,
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
                        "open": 4150.0, "close": 4160.0})
    decision = get_trading_decision(
        bar_1h=bar_1h, bar_5m=bar_5m, current_price=4160.0,
        liquidity_period="OVERLAP", bar_1d=bar_1d, gbpusd_rate=1.3376,
    )
    print(format_decision_for_display(decision))
    log.info("Arthur self-test complete.")
