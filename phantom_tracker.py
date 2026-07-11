"""
phantom_tracker.py — Phantom P&L Tracker
Albion Trading Desk
Records every STAY OUT decision and scores it with hindsight.
Part of the Morgan self-improvement loop.
All times UTC.

Concurrency note: a module-level lock guards every CSV read-modify-write so
that multiple background checkpoint threads (and, on CryptoTrader, the BTC and
ETH loops sharing one file) cannot corrupt phantom_trades.csv.
"""

import csv
import os
import threading
import time
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Path to CSV (relative to repo root)
PHANTOM_CSV = os.path.join(os.path.dirname(__file__), 'logs', 'phantom_trades.csv')

# Serialises all reads/writes of the CSV across threads.
_csv_lock = threading.Lock()

# CSV column headers
FIELDNAMES = [
    'timestamp',          # UTC ISO format
    'market',             # e.g. FTSE, GOLD, BTC, ETH, US500
    'direction_blocked',  # LONG or SHORT
    'price_at_decision',  # float — price when STAY OUT was called
    'confidence',         # float — Arthur's confidence score at decision
    'reason_for_stay_out',# string — reason code or description
    'price_30min',        # float — price 30 mins later (filled by background thread)
    'pnl_30min',          # float — P&L if trade had been taken (30min)
    'price_1hr',          # float — price 1 hour later
    'pnl_1hr',            # float — P&L if trade had been taken (1hr)
    'price_2hr',          # float — price 2 hours later
    'pnl_2hr',            # float — P&L if trade had been taken (2hr)
    'verdict',            # CORRECT / WRONG / NEUTRAL (filled after 2hr check)
    'morgan_processed',   # 'True' once Morgan has applied individual feedback ('' = not yet)
]

# Verdict thresholds (points)
VERDICT_THRESHOLD = 10  # price must move >10 pts to be CORRECT or WRONG


def _ensure_csv_exists():
    """Create logs directory and CSV with headers if not present."""
    os.makedirs(os.path.dirname(PHANTOM_CSV), exist_ok=True)
    if not os.path.exists(PHANTOM_CSV):
        with open(PHANTOM_CSV, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
        logger.info("phantom_tracker: Created phantom_trades.csv")


def _calculate_pnl(direction_blocked, price_at_decision, price_later):
    """
    Calculate P&L if the blocked trade had been taken.
    LONG blocked: profit if price went UP (we missed a winner)
    SHORT blocked: profit if price went DOWN (we missed a winner)
    Returns float (positive = we missed a profit, negative = we avoided a loss)
    """
    if price_later is None or price_at_decision is None:
        return None
    try:
        price_later = float(price_later)
        price_at_decision = float(price_at_decision)
    except (TypeError, ValueError):
        return None
    if direction_blocked == 'LONG':
        return round(price_later - price_at_decision, 2)
    elif direction_blocked == 'SHORT':
        return round(price_at_decision - price_later, 2)
    return None


def _calculate_verdict(direction_blocked, price_at_decision, price_1hr):
    """
    CORRECT: price moved >10 pts AGAINST our blocked entry direction
             (we were right to stay out — it would have been a loss)
    WRONG:   price moved >10 pts IN FAVOUR of our blocked entry direction
             (we were wrong to stay out — it would have been a profit)
    NEUTRAL: price moved <10 pts either way
    """
    if price_1hr is None or price_at_decision is None:
        return 'NEUTRAL'
    try:
        price_1hr = float(price_1hr)
        price_at_decision = float(price_at_decision)
    except (TypeError, ValueError):
        return 'NEUTRAL'

    if direction_blocked == 'LONG':
        move = price_1hr - price_at_decision
    elif direction_blocked == 'SHORT':
        move = price_at_decision - price_1hr
    else:
        return 'NEUTRAL'

    if move > VERDICT_THRESHOLD:
        return 'WRONG'    # Would have been profitable — we missed it
    elif move < -VERDICT_THRESHOLD:
        return 'CORRECT'  # Would have been a loss — right to stay out
    else:
        return 'NEUTRAL'


def _get_current_price(market):
    """
    Fetch current price for the given market.
    Each system implements its own price source — this is a stub
    that each repo's integration overrides by passing get_price_fn.
    Returns float or None.
    """
    logger.warning("phantom_tracker: _get_current_price not implemented for %s", market)
    return None


def _update_row(row_index, market, direction_blocked,
                price_at_decision, get_price_fn):
    """
    Background thread: waits 30min, 1hr, 2hr then updates the CSV row.
    get_price_fn: callable that returns current float price for market.
    """
    checkpoints = [
        (30 * 60,  'price_30min', 'pnl_30min'),
        (60 * 60,  'price_1hr',   'pnl_1hr'),
        (120 * 60, 'price_2hr',   'pnl_2hr'),
    ]

    for wait_seconds, price_col, pnl_col in checkpoints:
        time.sleep(wait_seconds)

        try:
            price = get_price_fn(market)
            pnl = _calculate_pnl(direction_blocked, price_at_decision, price)

            with _csv_lock:
                # Read all rows, update target row, rewrite CSV
                with open(PHANTOM_CSV, 'r', newline='') as f:
                    rows = list(csv.DictReader(f))

                if row_index < len(rows):
                    rows[row_index][price_col] = price
                    rows[row_index][pnl_col] = pnl

                    # Calculate verdict after 1hr check (primary verdict point)
                    if price_col == 'price_1hr':
                        rows[row_index]['verdict'] = _calculate_verdict(
                            direction_blocked, price_at_decision, price
                        )

                    with open(PHANTOM_CSV, 'w', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                        writer.writeheader()
                        writer.writerows(rows)

                    logger.info(
                        "phantom_tracker: Updated %s %s %s=%s %s=%s",
                        market, direction_blocked, price_col, price, pnl_col, pnl
                    )

        except Exception as e:
            logger.error("phantom_tracker: Error updating %s: %s", price_col, e)


def record_decision(market, direction_blocked, price_at_decision,
                    confidence, reason_for_stay_out, get_price_fn=None):
    """
    Call this after every STAY OUT decision in the main loop.

    Args:
        market:              str  — e.g. 'FTSE', 'GOLD', 'BTC', 'ETH', 'US500'
        direction_blocked:   str  — 'LONG' or 'SHORT'
        price_at_decision:   float — current market price
        confidence:          float — Arthur's confidence score
        reason_for_stay_out: str  — reason code or description
        get_price_fn:        callable(market) → float — price fetcher
                             (pass in Merlin's price function)
    """
    _ensure_csv_exists()

    if get_price_fn is None:
        get_price_fn = _get_current_price

    timestamp = datetime.now(timezone.utc).isoformat()

    new_row = {
        'timestamp':           timestamp,
        'market':              market,
        'direction_blocked':   direction_blocked,
        'price_at_decision':   price_at_decision,
        'confidence':          confidence,
        'reason_for_stay_out': reason_for_stay_out,
        'price_30min':         '',
        'pnl_30min':           '',
        'price_1hr':           '',
        'pnl_1hr':             '',
        'price_2hr':           '',
        'pnl_2hr':             '',
        'verdict':             'PENDING',
        'morgan_processed':    '',
    }

    # Append row and capture its index atomically so concurrent recorders
    # (e.g. BTC and ETH) can't hand the same index to two background threads.
    with _csv_lock:
        with open(PHANTOM_CSV, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(new_row)
        with open(PHANTOM_CSV, 'r', newline='') as f:
            row_index = sum(1 for _ in csv.DictReader(f)) - 1

    logger.info(
        "phantom_tracker: Recorded STAY OUT — %s %s @ %s confidence=%s reason=%s",
        market, direction_blocked, price_at_decision, confidence, reason_for_stay_out
    )

    # Launch background thread to check prices at +30min, +1hr, +2hr
    thread = threading.Thread(
        target=_update_row,
        args=(row_index, market, direction_blocked,
              price_at_decision, get_price_fn),
        daemon=True,
        name="PhantomTracker-%s-%s" % (market, timestamp)
    )
    thread.start()


def get_summary(last_n=10, reason=None):
    """
    Returns summary stats for the last N completed decisions.
    Used by Morgan integration (Stage 3).
    Returns dict with quality_score, correct, wrong, neutral, total, judged.

    quality_score is based ONLY on judged (CORRECT/WRONG) decisions --
    NEUTRAL (chop) is excluded from the denominator so directionless
    markets do not read as judgment failures.
    If `reason` is given, only rows whose reason_for_stay_out matches are
    counted (e.g. 'ARTHUR_STAY_OUT' to exclude hard Lancelot blocks).
    """
    _ensure_csv_exists()

    try:
        with _csv_lock:
            with open(PHANTOM_CSV, 'r', newline='') as f:
                rows = list(csv.DictReader(f))

        # Only count completed verdicts (not PENDING)
        completed = [r for r in rows if r.get('verdict')
                     in ('CORRECT', 'WRONG', 'NEUTRAL')]
        if reason is not None:
            completed = [r for r in completed
                         if r.get('reason_for_stay_out') == reason]
        last_n_rows = completed[-last_n:]

        if not last_n_rows:
            return {
                'quality_score': None,
                'correct': 0,
                'wrong': 0,
                'neutral': 0,
                'total': 0,
                'judged': 0
            }

        correct = sum(1 for r in last_n_rows if r['verdict'] == 'CORRECT')
        wrong   = sum(1 for r in last_n_rows if r['verdict'] == 'WRONG')
        neutral = sum(1 for r in last_n_rows if r['verdict'] == 'NEUTRAL')
        total   = len(last_n_rows)

        # Only score genuine hits/misses -- NEUTRAL is chop, not a judgment failure
        judged = [r for r in last_n_rows if r['verdict'] in ('CORRECT', 'WRONG')]
        quality_score = (
            round(sum(1 for r in judged if r['verdict'] == 'CORRECT')
                  / len(judged) * 100, 1)
            if judged else None
        )

        return {
            'quality_score': quality_score,
            'correct': correct,
            'wrong': wrong,
            'neutral': neutral,
            'total': total,          # all completed verdicts
            'judged': len(judged)    # CORRECT + WRONG only
        }

    except Exception as e:
        logger.error("phantom_tracker: get_summary error: %s", e)
        return {'quality_score': None, 'correct': 0,
                'wrong': 0, 'neutral': 0, 'total': 0, 'judged': 0}


def resolve_stale_pending(get_historical_price_fn):
    """
    Called on system startup.
    Scans phantom_trades.csv for PENDING rows older than 2 hours and resolves
    them retrospectively using historical price data from Merlin.

    Args:
        get_historical_price_fn: callable(market, timestamp) -> float or None
            Should return the market price at a given UTC datetime.
            Pass in Merlin's historical price function.
    """
    _ensure_csv_exists()

    try:
        with _csv_lock:
            with open(PHANTOM_CSV, 'r', newline='') as f:
                rows = list(csv.DictReader(f))
    except Exception as e:
        logger.error("phantom_tracker: resolve_stale_pending read error: %s", e)
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=2)
    changed = False

    for row in rows:
        if row.get('verdict') != 'PENDING':
            continue

        try:
            decision_time = datetime.fromisoformat(
                row['timestamp'].replace('Z', '+00:00')
            )
            if decision_time.tzinfo is None:
                decision_time = decision_time.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        # Only process rows older than 2 hours
        if decision_time > cutoff:
            continue

        market = row.get('market', '')
        direction = row.get('direction_blocked', '')
        try:
            price_at_decision = float(row.get('price_at_decision', 0) or 0)
        except (TypeError, ValueError):
            continue

        logger.info(
            "phantom_tracker: Resolving stale PENDING -- %s %s @ %s UTC",
            market, direction, decision_time
        )

        checkpoints = [
            (30,  'price_30min', 'pnl_30min'),
            (60,  'price_1hr',   'pnl_1hr'),
            (120, 'price_2hr',   'pnl_2hr'),
        ]

        for mins, price_col, pnl_col in checkpoints:
            check_time = decision_time + timedelta(minutes=mins)

            # Don't try to fetch future prices
            if check_time > now:
                continue
            # Only fetch if not already filled
            if row.get(price_col):
                continue

            try:
                price = get_historical_price_fn(market, check_time)
                if price is not None:
                    pnl = _calculate_pnl(direction, price_at_decision, price)
                    row[price_col] = price
                    row[pnl_col] = pnl
                    changed = True
            except Exception as e:
                logger.warning(
                    "phantom_tracker: Could not fetch %s price at %s: %s",
                    market, check_time, e
                )

        # Calculate verdict once the 1hr price is available
        if row.get('price_1hr') and row.get('verdict') == 'PENDING':
            try:
                price_1hr = float(row['price_1hr'])
                row['verdict'] = _calculate_verdict(
                    direction, price_at_decision, price_1hr
                )
                changed = True
                logger.info(
                    "phantom_tracker: Resolved %s %s -> %s",
                    market, direction, row['verdict']
                )
            except Exception as e:
                logger.warning("phantom_tracker: Verdict calc error: %s", e)

    if changed:
        try:
            with _csv_lock:
                with open(PHANTOM_CSV, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                    writer.writeheader()
                    writer.writerows(rows)
            logger.info("phantom_tracker: Stale PENDING rows resolved.")
        except Exception as e:
            logger.error("phantom_tracker: resolve_stale_pending write error: %s", e)
    else:
        logger.info("phantom_tracker: No stale PENDING rows found.")


# Guard so the watchdog is only ever started once per process.
_watchdog_thread = None


def start_watchdog(get_historical_price_fn, interval_minutes=15):
    """
    Starts a continuous background watchdog thread.
    Runs resolve_stale_pending() every interval_minutes so that PENDING rows
    resolve dynamically throughout the day -- no restart required.

    Call once after Merlin is initialised. Runs for the lifetime of the
    process. Idempotent: if a watchdog is already alive, subsequent calls are
    ignored so no duplicate threads are ever started.

    Args:
        get_historical_price_fn: callable(market, timestamp) -> float or None.
            Merlin's historical price function.
        interval_minutes: how often to scan (default 15).
    """
    global _watchdog_thread
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        logger.info(
            "phantom_tracker: Watchdog already running -- not starting a second."
        )
        return _watchdog_thread

    def _watchdog_loop():
        logger.info(
            "phantom_tracker: Watchdog started -- scanning every %d mins "
            "for stale PENDING rows.", interval_minutes
        )
        while True:
            try:
                resolve_stale_pending(get_historical_price_fn)
            except Exception as e:
                logger.error("phantom_tracker: Watchdog error: %s", e)
            time.sleep(interval_minutes * 60)

    _watchdog_thread = threading.Thread(
        target=_watchdog_loop,
        daemon=True,
        name="PhantomWatchdog",
    )
    _watchdog_thread.start()
    logger.info("phantom_tracker: Watchdog thread started.")
    return _watchdog_thread


def get_unprocessed_verdicts():
    """
    Return judged (CORRECT/WRONG) phantom rows not yet processed by Morgan's
    individual-feedback poller. Thread-safe (shares _csv_lock). Each returned
    dict includes at least 'timestamp', 'verdict', 'pnl_1hr'. NEUTRAL and
    PENDING rows are excluded (they carry no individual judgment signal).
    """
    _ensure_csv_exists()
    try:
        with _csv_lock:
            with open(PHANTOM_CSV, 'r', newline='') as f:
                rows = list(csv.DictReader(f))
    except Exception as e:
        logger.error("phantom_tracker: get_unprocessed_verdicts read error: %s", e)
        return []
    out = []
    for r in rows:
        if r.get('morgan_processed') == 'True':
            continue
        if r.get('verdict') in ('CORRECT', 'WRONG'):
            out.append(r)
    return out


def mark_processed(timestamps):
    """
    Mark the given decision timestamps as processed by Morgan (morgan_processed
    = 'True') and rewrite the CSV. Thread-safe (shares _csv_lock). Rows written
    from an older header gain the new column as needed.
    """
    if not timestamps:
        return
    ts_set = set(timestamps)
    _ensure_csv_exists()
    try:
        with _csv_lock:
            with open(PHANTOM_CSV, 'r', newline='') as f:
                rows = list(csv.DictReader(f))
            changed = False
            for r in rows:
                if r.get('timestamp') in ts_set and r.get('morgan_processed') != 'True':
                    r['morgan_processed'] = 'True'
                    changed = True
            if changed:
                with open(PHANTOM_CSV, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                    writer.writeheader()
                    writer.writerows(rows)
    except Exception as e:
        logger.error("phantom_tracker: mark_processed error: %s", e)
