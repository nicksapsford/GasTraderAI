"""
GasTrader AI -- performance_gas.py  (Morgan)
Performance tracker and confidence engine for Arthur.
Tracks win rate by direction and by liquidity period (Asian/London/NY/Overlap).
"""

import json
import logging
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import phantom_tracker

log = logging.getLogger("GasTrader.Morgan")


# ── Morgan individual phantom feedback: persistent confidence store ───────────
_MORGAN_STATE_PATH = os.path.join(os.path.dirname(__file__), 'logs', 'morgan_confidence.json')
_morgan_lock = threading.Lock()
_morgan_confidence = None


def _load_morgan_confidence():
    global _morgan_confidence
    if _morgan_confidence is None:
        try:
            with open(_MORGAN_STATE_PATH) as f:
                _morgan_confidence = float(json.load(f).get('confidence', 50.0))
        except Exception:
            _morgan_confidence = 50.0
    return _morgan_confidence


def get_confidence():
    with _morgan_lock:
        return _load_morgan_confidence()


def set_confidence(value):
    global _morgan_confidence
    with _morgan_lock:
        _morgan_confidence = max(0.0, min(100.0, float(value)))
        try:
            os.makedirs(os.path.dirname(_MORGAN_STATE_PATH), exist_ok=True)
            with open(_MORGAN_STATE_PATH, 'w') as f:
                json.dump({'confidence': _morgan_confidence}, f)
        except Exception as e:
            log.warning("Morgan: could not persist confidence: %s", e)
        log.info("Morgan: confidence set to %.1f", _morgan_confidence)
        return _morgan_confidence


def apply_phantom_verdict_feedback(verdict, pnl_1hr, current_confidence):
    """Compute Morgan's individual-verdict confidence adjustment.

    NEUTRAL verdicts carry no signal (adjustment 0.0). For CORRECT/WRONG the raw
    magnitude scales with how big the missed/avoided move was:
        raw = clamp(abs(pnl_1hr) / 50, 0.5, 2.0)
    CORRECT (we were right to stay out) nudges confidence UP by raw;
    WRONG (we missed a profit) nudges it DOWN by raw.
    Returns (adjustment, reason).
    """
    if verdict == 'NEUTRAL':
        return 0.0, "NEUTRAL verdict -- no confidence signal"
    try:
        pnl = abs(float(pnl_1hr))
    except (TypeError, ValueError):
        pnl = 0.0
    raw = max(0.5, min(2.0, pnl / 50.0))
    if verdict == 'CORRECT':
        adjustment = raw
        reason = "CORRECT stay-out (avoided %.1f move) -> confidence +%.2f" % (pnl, raw)
    elif verdict == 'WRONG':
        adjustment = -raw
        reason = "WRONG stay-out (missed %.1f move) -> confidence -%.2f" % (pnl, raw)
    else:
        return 0.0, "Unknown verdict '%s' -- no confidence signal" % verdict
    log.info("Morgan phantom feedback: %s (conf %.1f -> %.1f)",
             reason, current_confidence, max(0.0, min(100.0, current_confidence + adjustment)))
    return adjustment, reason


def process_new_phantom_verdicts(get_confidence_fn=None, set_confidence_fn=None):
    """Start a daemon poller that applies Morgan's individual phantom feedback.

    Every 300s it pulls unprocessed CORRECT/WRONG phantom verdicts, applies the
    per-verdict confidence adjustment, persists the new confidence, and marks
    those rows processed so they are never double-counted.
    """
    get_fn = get_confidence_fn or get_confidence
    set_fn = set_confidence_fn or set_confidence

    def _loop():
        while True:
            try:
                rows = phantom_tracker.get_unprocessed_verdicts()
                if rows:
                    processed = []
                    for r in rows:
                        try:
                            conf = get_fn()
                            adjustment, reason = apply_phantom_verdict_feedback(
                                r.get('verdict'), r.get('pnl_1hr'), conf)
                            new_conf = max(0.0, min(100.0, conf + adjustment))
                            set_fn(new_conf)
                            ts = r.get('timestamp')
                            if ts:
                                processed.append(ts)
                        except Exception as e:
                            log.warning("Morgan: phantom row feedback failed: %s", e)
                    if processed:
                        phantom_tracker.mark_processed(processed)
                        log.info("Morgan: processed %d phantom verdict(s)", len(processed))
            except Exception as e:
                log.warning("Morgan: phantom poller loop error: %s", e)
            time.sleep(300)

    t = threading.Thread(target=_loop, name="MorganPhantomPoller", daemon=True)
    t.start()
    log.info("Morgan: phantom verdict poller started (MorganPhantomPoller, 300s)")
    return t


def get_stay_out_adjustment():
    """Morgan self-improvement: nudge confidence by STAY OUT decision quality.
    >70% correct -> +5 ; <40% correct -> -5 ; 40-70% or <5 samples -> 0."""
    summary = phantom_tracker.get_summary(last_n=10)
    if summary['total'] < 5:
        return 0.0
    quality = summary['quality_score']
    if quality is None:
        return 0.0
    if quality > 70:
        log.info("Morgan: STAY OUT quality %s%% -> confidence +5", quality)
        return 5.0
    if quality < 40:
        log.info("Morgan: STAY OUT quality %s%% -> confidence -5", quality)
        return -5.0
    return 0.0

LOG_DIR    = Path(__file__).parent / "logs"
TRADES_LOG = LOG_DIR / "gas_trades.csv"
REVIEW_DIR = LOG_DIR

LIQUIDITY_PERIODS = ["ASIAN", "LONDON", "OVERLAP", "NEW_YORK"]

_cache: dict = {}
_cache_valid = False


def invalidate_cache() -> None:
    global _cache_valid
    _cache_valid = False


def _load_trades(trades_log: Path = TRADES_LOG) -> pd.DataFrame:
    if not trades_log.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(trades_log)
        if df.empty:
            return df
        df["pnl_gbp"] = pd.to_numeric(df["pnl_gbp"], errors="coerce").fillna(0)
        df["_dt"]     = pd.to_datetime(df["entry_time"], errors="coerce")
        return df
    except Exception as exc:
        log.warning("Could not load trades: %s", exc)
        return pd.DataFrame()


def _compute_confidence(df: pd.DataFrame) -> dict:
    """Confidence score 0-100 based on recent performance. Conservative mode below 25."""
    if df.empty or len(df) < 5:
        return {
            "confidence_score":  int(max(0, min(100, 50 + get_stay_out_adjustment()))),
            "confidence_level": "MEDIUM", "conservative": False,
            "total_trades": 0, "win_rate": 0.0, "recent_5": [],
            "streak_type": "", "streak_count": 0,
            "strongest_conditions": [], "weakest_conditions": [],
        }

    pnls     = df["pnl_gbp"].values
    wins     = sum(1 for p in pnls if p >= 0)
    total    = len(pnls)
    win_rate = wins / total * 100

    recent_20 = df.tail(20)["pnl_gbp"].values
    recent_5  = ["WIN" if p >= 0 else "LOSS" for p in df.tail(5)["pnl_gbp"].values]
    r20_wins  = sum(1 for p in recent_20 if p >= 0)
    r20_wr    = r20_wins / len(recent_20) * 100 if recent_20.size > 0 else 50.0

    avg_win  = sum(p for p in pnls if p > 0) / max(1, wins)
    avg_loss = abs(sum(p for p in pnls if p < 0)) / max(1, total - wins)
    rr       = avg_win / avg_loss if avg_loss > 0 else 1.0

    streak_type, streak_count = "", 0
    for p in reversed(pnls):
        is_win = p >= 0
        if streak_count == 0:
            streak_type, streak_count = ("WIN" if is_win else "LOSS"), 1
        elif (streak_type == "WIN" and is_win) or (streak_type == "LOSS" and not is_win):
            streak_count += 1
        else:
            break

    score = 50.0
    score += (r20_wr - 50) * 0.6
    score += (rr - 1.0) * 5.0
    if streak_type == "WIN"  and streak_count >= 3: score += 10
    if streak_type == "LOSS" and streak_count >= 3: score -= 15
    score = max(0, min(100, round(score)))
    score = int(max(0, min(100, score + get_stay_out_adjustment())))
    # Morgan individual phantom feedback delta (distinct from the aggregate
    # stay-out quality nudge above -- no double-counting).
    score = int(max(0, min(100, score + (get_confidence() - 50.0))))

    if score >= 75:   level = "HIGH"
    elif score >= 50: level = "MEDIUM"
    elif score >= 25: level = "LOW"
    else:             level = "VERY_LOW"

    strongest, weakest = [], []
    if total >= 10:
        for direction in ["LONG", "SHORT"]:
            sub = df[df["direction"] == direction]
            if len(sub) >= 5:
                wr_dir = sum(1 for p in sub["pnl_gbp"] if p >= 0) / len(sub) * 100
                label  = f"{direction}: {wr_dir:.0f}% WR ({len(sub)} trades)"
                if wr_dir >= 60:
                    strongest.append(label)
                elif wr_dir < 45:
                    weakest.append(label)
        if "liquidity_period" in df.columns:
            for period in LIQUIDITY_PERIODS:
                sub = df[df["liquidity_period"] == period]
                if len(sub) >= 5:
                    wr_p  = sum(1 for p in sub["pnl_gbp"] if p >= 0) / len(sub) * 100
                    label = f"{period}: {wr_p:.0f}% WR ({len(sub)} trades)"
                    if wr_p >= 60:
                        strongest.append(label)
                    elif wr_p < 45:
                        weakest.append(label)

    return {
        "confidence_score":     score,
        "confidence_level":     level,
        "conservative":         score < 25,
        "total_trades":         total,
        "win_rate":             round(win_rate, 1),
        "recent_5":             list(reversed(recent_5)),
        "streak_type":          streak_type,
        "streak_count":         streak_count,
        "strongest_conditions": strongest,
        "weakest_conditions":   weakest,
    }


def _compute_direction_liquidity_stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"direction": {}, "liquidity": {}}
    direction_stats = {}
    for d in ["LONG", "SHORT"]:
        sub = df[df["direction"] == d]
        if len(sub) == 0:
            continue
        wins = int(sum(1 for p in sub["pnl_gbp"] if p >= 0))
        direction_stats[d] = {
            "trades": int(len(sub)), "wins": wins,
            "win_rate": round(wins / len(sub) * 100, 1),
            "net_pnl": round(float(sub["pnl_gbp"].sum()), 2),
        }
    liquidity_stats = {}
    if "liquidity_period" in df.columns:
        for p in LIQUIDITY_PERIODS:
            sub = df[df["liquidity_period"] == p]
            if len(sub) == 0:
                continue
            wins = int(sum(1 for x in sub["pnl_gbp"] if x >= 0))
            liquidity_stats[p] = {
                "trades": int(len(sub)), "wins": wins,
                "win_rate": round(wins / len(sub) * 100, 1),
                "net_pnl": round(float(sub["pnl_gbp"].sum()), 2),
            }
    return {"direction": direction_stats, "liquidity": liquidity_stats}


def get_performance_context(trades_log: Path = TRADES_LOG) -> str:
    """Formatted performance context string for Arthur."""
    df   = _load_trades(trades_log)
    perf = _compute_confidence(df)
    lines = [
        "SELF PERFORMANCE AWARENESS (Morgan)",
        f"  Confidence:     {perf['confidence_score']}/100 {perf['confidence_level']}",
        f"  Conservative:   {'YES -- STAY_OUT mode' if perf['conservative'] else 'No'}",
        f"  Total trades:   {perf['total_trades']}",
        f"  Win rate:       {perf['win_rate']}%",
        f"  Current streak: {perf['streak_count']} {perf['streak_type']}",
        f"  Recent (last 5): {' | '.join(perf['recent_5']) if perf['recent_5'] else 'no trades yet'}",
    ]
    if perf["strongest_conditions"]:
        lines.append("  Strongest: " + ", ".join(perf["strongest_conditions"]))
    if perf["weakest_conditions"]:
        lines.append("  Weakest:   " + ", ".join(perf["weakest_conditions"]))
    lines.append(
        "\n  Confidence guide: HIGH(75+)=normal, MED(50-74)=raise bar, "
        "LOW(25-49)=exceptional only, VERY_LOW(<25)=STAY OUT hard rule"
    )
    return "\n".join(lines)


def get_perf_dashboard_dict(trades_log: Path = TRADES_LOG) -> dict:
    """Performance data dict for dashboard rendering (includes breakdown.liquidity)."""
    global _cache, _cache_valid
    if _cache_valid:
        return _cache
    df   = _load_trades(trades_log)
    perf = _compute_confidence(df)
    breakdown = _compute_direction_liquidity_stats(df)
    _cache = {**perf, "breakdown": breakdown}
    _cache_valid = True
    return _cache


def generate_milestone_review(trades_log: Path, milestone_num: int) -> None:
    """Save a milestone review to logs/arthur_gas_review_XX.txt every 50 trades."""
    df = _load_trades(trades_log)
    if df.empty:
        return
    perf      = _compute_confidence(df)
    breakdown = _compute_direction_liquidity_stats(df)
    review_file = REVIEW_DIR / f"arthur_gas_review_{milestone_num:02d}.txt"
    lines = [
        "=" * 60,
        f"GasTrader AI -- Arthur Milestone Review #{milestone_num}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Trades completed: {perf['total_trades']}",
        "=" * 60,
        "",
        "PERFORMANCE SUMMARY",
        f"  Win rate:       {perf['win_rate']}%",
        f"  Confidence:     {perf['confidence_score']}/100 {perf['confidence_level']}",
        f"  Current streak: {perf['streak_count']} {perf['streak_type']}",
        "",
        "DIRECTION BREAKDOWN",
    ]
    for d, stats in breakdown["direction"].items():
        lines.append(f"  {d}: {stats['trades']} trades | {stats['win_rate']}% WR | net GBP {stats['net_pnl']:+.2f}")
    lines.append("\nLIQUIDITY PERIOD BREAKDOWN")
    for p, stats in breakdown["liquidity"].items():
        lines.append(f"  {p}: {stats['trades']} trades | {stats['win_rate']}% WR | net GBP {stats['net_pnl']:+.2f}")
    if perf["strongest_conditions"]:
        lines.append("\nSTRONGEST CONDITIONS")
        for c in perf["strongest_conditions"]:
            lines.append(f"  + {c}")
    if perf["weakest_conditions"]:
        lines.append("\nWEAKEST CONDITIONS (consider avoiding)")
        for c in perf["weakest_conditions"]:
            lines.append(f"  - {c}")
    lines.append("\n" + "=" * 60)
    with open(review_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("Milestone review saved: %s", review_file)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Morgan self-test (Gas)")
    log.info("Performance context:\n%s", get_performance_context())
    log.info("Morgan self-test complete.")
