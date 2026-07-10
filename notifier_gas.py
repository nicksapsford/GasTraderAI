"""
GasTrader AI -- notifier_gas.py  (Percival)
Pushover push notifications. All failures are silent/logged.
Loads credentials from .env (shared Pushover account). All notifications
prefixed [GAS].
"""

import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

_ENV_PATH = BASE_DIR / ".env"
if _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH)
else:
    load_dotenv()

log = logging.getLogger("GasTrader.Percival")

_PUSHOVER_API = "https://api.pushover.net/1/messages.json"
_USER         = os.getenv("PUSHOVER_USER_KEY",  "")
_TOKEN        = os.getenv("PUSHOVER_API_TOKEN", "")

_P_NORMAL = 0
_P_HIGH   = 1


def _send(title: str, message: str, priority: int = _P_NORMAL) -> None:
    if not _USER or not _TOKEN:
        log.debug("Pushover not configured -- skipping: %s", title)
        return
    try:
        resp = requests.post(
            _PUSHOVER_API,
            data={"token": _TOKEN, "user": _USER, "title": title,
                  "message": message, "priority": priority},
            timeout=5,
        )
        if resp.status_code == 200:
            log.debug("Notification sent: %s", title)
        else:
            log.warning("Pushover HTTP %d for: %s", resp.status_code, title)
    except Exception as exc:
        log.warning("Pushover notification failed (%s): %s", title, exc)


# ── Public notification functions ─────────────────────────────────────────────

def notify_trade_opened(direction, entry_price, stop_loss, take_profit, stake,
                        liquidity_period="", size_oz=0.0) -> None:
    _send(
        title   = "[GAS] Trade Opened -- GasTrader AI",
        message = (
            f"{direction} opened at ${entry_price:,.2f}\n"
            f"Stop: ${stop_loss:,.2f} | Target: ${take_profit:,.2f}\n"
            f"Size: {size_oz:.2f}oz | Stake: £{stake:.4f}/pt | {liquidity_period}"
        ),
    )


def notify_trade_closed_win(direction, exit_price, points_gained, pnl_gbp,
                            capital, reason) -> None:
    _send(
        title   = "[GAS] Trade WON -- GasTrader AI",
        message = (
            f"{direction} closed at ${exit_price:,.2f}\n"
            f"Points: +{points_gained:.1f} | P&L: +£{pnl_gbp:.2f}\n"
            f"Capital: £{capital:.2f} | Reason: {reason}"
        ),
    )


def notify_trade_closed_loss(direction, exit_price, points_gained, pnl_gbp,
                             capital, reason) -> None:
    _send(
        title   = "[GAS] Trade Lost -- GasTrader AI",
        message = (
            f"{direction} closed at ${exit_price:,.2f}\n"
            f"Points: {points_gained:.1f} | P&L: -£{abs(pnl_gbp):.2f}\n"
            f"Capital: £{capital:.2f} | Reason: {reason}"
        ),
    )


def notify_kill_switch_triggered(tier, reason, wait_hours, daily_pnl, capital) -> None:
    _send(
        title   = f"[GAS] KILL SWITCH Tier {tier} -- GasTrader AI",
        message = (
            f"{reason}\nDaily P&L: £{daily_pnl:+.2f}\n"
            f"Auto-resume in {wait_hours}h | Capital: £{capital:.2f}"
        ),
        priority = _P_HIGH,
    )


def notify_kill_switch_reset(tier, wait_hours, capital) -> None:
    _send(
        title   = "[GAS] Trading Resuming -- GasTrader AI",
        message = (
            f"Kill switch reset after {wait_hours}h cooldown (Tier {tier}).\n"
            f"Capital: £{capital:.2f}. Watching for Gas setups."
        ),
    )


def notify_system_startup(capital, mode="PAPER") -> None:
    _send(
        title   = f"[GAS] GasTrader AI Started ({mode})",
        message = (
            f"GasTrader AI is live.\nCapital: £{capital:.2f} | Mode: {mode}\n"
            f"Trading natural gas (Natural Gas) via Capital.com spread betting."
        ),
    )


def notify_system_shutdown(capital) -> None:
    _send(
        title   = "[GAS] GasTrader AI Shutdown",
        message = f"GasTrader AI stopped cleanly.\nFinal capital: £{capital:.2f}",
    )


def notify_calendar_block(event_name, mins_remaining) -> None:
    _send(
        title   = "[GAS] Calendar Block Active",
        message = f"Trading paused: {event_name}\n{mins_remaining} min remaining. Will resume automatically.",
    )


def notify_daily_summary(date_str, trades, pnl_gbp, capital, win_rate) -> None:
    _send(
        title   = f"[GAS] Daily Summary {date_str}",
        message = (
            f"Trades: {trades} | P&L: £{pnl_gbp:+.2f}\n"
            f"Win rate: {win_rate:.1f}% | Capital: £{capital:.2f}"
        ),
    )


def notify_milestone_review(milestone_num) -> None:
    _send(
        title   = f"[GAS] Milestone Review #{milestone_num} -- Arthur",
        message = (
            f"Arthur has completed his {milestone_num * 50}-trade review.\n"
            f"Check logs/arthur_gas_review_{milestone_num:02d}.txt for insights."
        ),
    )


def notify_system_error(error_msg) -> None:
    _send(
        title   = "[GAS] System Error -- GasTrader AI",
        message = f"Error detected:\n{error_msg[:200]}",
        priority = _P_HIGH,
    )
