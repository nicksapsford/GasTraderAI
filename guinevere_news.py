"""
guinevere_news.py — Natural Gas News Sentiment Module
Albion Trading Desk — NaturalGasTrader A.I.
Fetches natural-gas-related news from Currents API and
EIA Gas Storage data to inform Arthur's confidence.
Weather and storage drive the sharp moves in gas.
All times UTC.
"""

import os
import csv
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Guinevere sentiment CSV audit trail ────────────────────
SENTIMENT_LOG = os.path.join(os.path.dirname(__file__), 'logs', 'guinevere_sentiment.csv')
SENTIMENT_FIELDNAMES = [
    'timestamp', 'sentiment', 'score',
    'headline_1', 'headline_2', 'headline_3', 'eia_window'
]


def save_sentiment(sentiment_data):
    """Append one sentiment snapshot row to SENTIMENT_LOG (CSV audit trail)."""
    try:
        headlines = sentiment_data.get('headlines', []) or []
        titles = []
        for h in headlines[:3]:
            if isinstance(h, dict):
                titles.append(h.get('title', ''))
            else:
                titles.append(str(h))
        while len(titles) < 3:
            titles.append('')
        os.makedirs(os.path.dirname(SENTIMENT_LOG), exist_ok=True)
        new_file = not os.path.exists(SENTIMENT_LOG)
        with open(SENTIMENT_LOG, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=SENTIMENT_FIELDNAMES)
            if new_file:
                writer.writeheader()
            writer.writerow({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'sentiment': sentiment_data.get('sentiment', ''),
                'score': sentiment_data.get('score', ''),
                'headline_1': titles[0],
                'headline_2': titles[1],
                'headline_3': titles[2],
                'eia_window': bool(sentiment_data.get('eia_window', False)),
            })
    except Exception as e:
        logger.warning(f"guinevere_news: could not append sentiment CSV: {e}")

# ── API Configuration ──────────────────────────────────────
CURRENTS_API_KEY = os.getenv('CURRENTS_API_KEY')
CURRENTS_BASE_URL = 'https://api.currentsapi.services/v1'

# ── Gas-specific keywords (tightened 17 Jul 2026 -- phrases, not single words) ──
# Hardcoded DEFAULTS / fallback; live values read from logs/guinevere_keywords.json.
SYSTEM_NAME = "GasTrader"
BULLISH_KEYWORDS = [
    "natural gas", "NatGas", "LNG", "Henry Hub",
    "gas storage draw", "gas inventory draw",
    "gas deficit", "below average storage",
    "cold snap", "winter storm", "polar vortex",
    "heating demand", "heat wave", "cooling demand",
    "hot summer", "extreme weather", "gas demand",
    "LNG export", "LNG terminal", "gas shortage",
    "Europe gas", "Asia gas demand", "gas supply disruption",
    "gas pipeline", "gas outage", "EIA gas draw"
]

BEARISH_KEYWORDS = [
    "gas storage build", "gas inventory build",
    "gas surplus", "above average storage",
    "mild weather", "warm winter", "cool summer",
    "below normal temperatures", "LNG glut", "LNG surplus",
    "gas production surge", "gas oversupply",
    "EIA gas build", "record gas production"
]

KEYWORDS_FILE       = os.path.join(os.path.dirname(__file__), 'logs', 'guinevere_keywords.json')
KEYWORD_CHANGE_LOG  = os.path.join(os.path.dirname(__file__), 'logs', 'guinevere_keyword_changes.log')
MACRO_FILE          = os.path.join(os.path.dirname(__file__), '..', 'RoundTableAI', 'logs', 'macro_sentiment.json')
_kw_cache = {'ts': None, 'bullish': None, 'bearish': None, 'last_updated': None, 'updated_by': None}

# News older than this is ignored
MAX_NEWS_AGE_HOURS = 4

# Cache to avoid hammering the API
_news_cache = {
    'timestamp': None,
    'sentiment': 'NEUTRAL',
    'score': 0,
    'headlines': [],
    'reason': 'No data yet'
}
CACHE_DURATION_MINUTES = 5


def _write_keywords_file(bullish, bearish, updated_by):
    os.makedirs(os.path.dirname(KEYWORDS_FILE), exist_ok=True)
    data = {'bullish': list(bullish), 'bearish': list(bearish),
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'updated_by': updated_by}
    with open(KEYWORDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return data


def _load_keywords(force=False):
    """Active keyword lists from logs/guinevere_keywords.json (5-min cache).
    Auto-initialises the file from the hardcoded defaults if it is missing."""
    now = datetime.now(timezone.utc)
    if (not force and _kw_cache['ts']
            and (now - _kw_cache['ts']).total_seconds() < 300):
        return _kw_cache
    data = None
    try:
        with open(KEYWORDS_FILE, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d.get('bullish'), list) and isinstance(d.get('bearish'), list):
            data = d
    except Exception:
        data = None
    if data is None:
        data = _write_keywords_file(BULLISH_KEYWORDS, BEARISH_KEYWORDS, 'defaults')
    _kw_cache.update(ts=now, bullish=data['bullish'], bearish=data['bearish'],
                     last_updated=data.get('last_updated'), updated_by=data.get('updated_by'))
    return _kw_cache


def get_keywords():
    """Public: current keyword lists + metadata (for the dashboard editor)."""
    kw = _load_keywords(force=True)
    return {'bullish': list(kw['bullish']), 'bearish': list(kw['bearish']),
            'last_updated': kw['last_updated'], 'updated_by': kw['updated_by']}


def _score_headline(title, description=''):
    """Score a headline: +1 per bullish keyword, -1 per bearish (case-insensitive
    phrase match). Keywords are the live editable set (logs/guinevere_keywords.json)."""
    text = (title + ' ' + (description or '')).lower()
    kw = _load_keywords()
    score = 0
    for keyword in kw['bullish']:
        if keyword.lower() in text:
            score += 1
    for keyword in kw['bearish']:
        if keyword.lower() in text:
            score -= 1
    return score


def _is_recent(published_at_str):
    """Check if article is within MAX_NEWS_AGE_HOURS."""
    try:
        published = datetime.fromisoformat(
            published_at_str.replace('Z', '+00:00')
        )
        age = datetime.now(timezone.utc) - published
        return age < timedelta(hours=MAX_NEWS_AGE_HOURS)
    except Exception:
        return False


def fetch_gas_sentiment():
    """
    Fetch latest gas news from Currents API.
    Returns dict with sentiment, score, headlines, reason.
    Caches result for CACHE_DURATION_MINUTES.
    """
    global _news_cache

    # Return cached result if fresh
    if _news_cache['timestamp']:
        age = datetime.now(timezone.utc) - _news_cache['timestamp']
        if age < timedelta(minutes=CACHE_DURATION_MINUTES):
            logger.debug("guinevere_news: Using cached sentiment")
            return _news_cache

    if not CURRENTS_API_KEY or CURRENTS_API_KEY == 'PASTE_YOUR_KEY_HERE':
        logger.warning("guinevere_news: No CURRENTS_API_KEY in .env")
        return {
            'sentiment': 'NEUTRAL',
            'score': 0,
            'headlines': [],
            'reason': 'No API key configured'
        }

    try:
        # Search for gas/energy news
        params = {
            'apiKey': CURRENTS_API_KEY,
            'keywords': 'natural gas OR LNG OR gas storage OR gas pipeline OR EIA gas',
            'language': 'en',
            'limit': 10
        }
        response = requests.get(
            f'{CURRENTS_BASE_URL}/search',
            params=params,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        articles = data.get('news', [])
        recent = [a for a in articles if _is_recent(
            a.get('published', '')
        )]

        if not recent:
            result = {
                'sentiment': 'NEUTRAL',
                'score': 0,
                'headlines': [],
                'reason': 'No recent gas news (last 4hrs)',
                'timestamp': datetime.now(timezone.utc)
            }
            _news_cache = result
            return result

        # Score recent headlines. Dedup by url/title first -- the news API can
        # return the same article several times, which would triple-count the
        # sentiment score and repeat the headline in the brief/dashboard (Snag 16).
        total_score = 0
        headlines = []
        seen = set()
        for article in recent:
            title = (article.get('title') or '').strip()
            key = (article.get('url') or article.get('link') or title).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            score = _score_headline(title, article.get('description', ''))
            total_score += score
            headlines.append({
                'title': title,
                'score': score,
                'published': article.get('published', '')
            })
            if len(headlines) >= 5:
                break

        # Determine overall sentiment
        if total_score >= 2:
            sentiment = 'BULLISH'
            reason = f"Gas news BULLISH (score +{total_score})"
        elif total_score <= -2:
            sentiment = 'BEARISH'
            reason = f"Gas news BEARISH (score {total_score})"
        else:
            sentiment = 'NEUTRAL'
            reason = f"Gas news NEUTRAL (score {total_score})"

        result = {
            'sentiment': sentiment,
            'score': total_score,
            'headlines': headlines,
            'reason': reason,
            'timestamp': datetime.now(timezone.utc)
        }
        _news_cache = result
        logger.info(f"guinevere_news: {reason}")
        result['eia_window'] = get_eia_gas_calendar_status()[0]
        try:
            save_sentiment(result)
        except Exception:
            pass
        return result

    except requests.exceptions.Timeout:
        logger.warning("guinevere_news: Currents API timeout")
        return {**_news_cache, 'reason': 'API timeout — using cache'}
    except Exception as e:
        logger.error(f"guinevere_news: Error fetching news: {e}")
        return {**_news_cache, 'reason': f'API error: {e}'}


def get_confidence_adjustment(direction):
    """
    Returns confidence adjustment based on news sentiment
    and the trade direction being considered.

    LONG + BULLISH news  → +8  (news supports entry)
    SHORT + BEARISH news → +8  (news supports entry)
    LONG + BEARISH news  → -8  (news opposes entry)
    SHORT + BULLISH news → -8  (news opposes entry)
    Any + NEUTRAL        →  0  (no adjustment)

    Args:
        direction: 'LONG' or 'SHORT'
    Returns:
        float: confidence adjustment
        str: reason string for logging
    """
    sentiment_data = fetch_gas_sentiment()
    sentiment = sentiment_data['sentiment']
    reason = sentiment_data['reason']

    if sentiment == 'NEUTRAL':
        return 0.0, f"Guinevere News: NEUTRAL — {reason}"

    if (direction == 'LONG' and sentiment == 'BULLISH') or \
       (direction == 'SHORT' and sentiment == 'BEARISH'):
        return 8.0, f"Guinevere News: +8 confidence — {reason}"

    if (direction == 'LONG' and sentiment == 'BEARISH') or \
       (direction == 'SHORT' and sentiment == 'BULLISH'):
        return -8.0, f"Guinevere News: -8 confidence — {reason}"

    return 0.0, f"Guinevere News: NEUTRAL — {reason}"


def get_eia_gas_calendar_status():
    """
    Returns True if today is EIA Natural Gas Storage day (Thursday)
    and we are within the high-volatility window (14:00-15:00 UTC).
    The EIA Gas Storage report releases Thursdays at 14:30 UTC.
    """
    now = datetime.now(timezone.utc)
    if now.weekday() == 3 and 14 <= now.hour < 15:
        return True, "EIA Gas Storage report window (Thu 14:30 UTC)"
    return False, ""
