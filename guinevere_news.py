"""
guinevere_news.py — Natural Gas News Sentiment Module
Albion Trading Desk — NaturalGasTrader A.I.
Fetches natural-gas-related news from Currents API and
EIA Gas Storage data to inform Arthur's confidence.
Weather and storage drive the sharp moves in gas.
All times UTC.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── API Configuration ──────────────────────────────────────
CURRENTS_API_KEY = os.getenv('CURRENTS_API_KEY')
CURRENTS_BASE_URL = 'https://api.currentsapi.services/v1'

# ── Gas-specific keywords (weather + storage drive sharp moves) ─────
BULLISH_KEYWORDS = [
    'storage draw', 'cold snap', 'cold weather', 'freeze',
    'pipeline disruption', 'lng export', 'lng terminal',
    'hurricane', 'tropical storm', 'supply cut', 'russia gas',
    'gas shortage', 'heating demand', 'polar vortex',
    'winter demand', 'production cut'
]

BEARISH_KEYWORDS = [
    'storage build', 'mild weather', 'warm winter',
    'record storage', 'lng glut', 'gas glut',
    'record production', 'shale boom', 'demand falls',
    'mild temperatures', 'above average storage',
    'pipeline restored', 'ceasefire'
]

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


def _score_headline(title, description=''):
    """
    Score a single headline for gas sentiment.
    Returns: positive int (bullish), negative (bearish), 0 (neutral)
    """
    text = (title + ' ' + (description or '')).lower()
    score = 0

    for keyword in BULLISH_KEYWORDS:
        if keyword in text:
            score += 1
            logger.debug(f"guinevere_news: BULLISH keyword '{keyword}' found")

    for keyword in BEARISH_KEYWORDS:
        if keyword in text:
            score -= 1
            logger.debug(f"guinevere_news: BEARISH keyword '{keyword}' found")

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

        # Score all recent headlines
        total_score = 0
        headlines = []
        for article in recent[:5]:  # Top 5 only
            score = _score_headline(
                article.get('title', ''),
                article.get('description', '')
            )
            total_score += score
            headlines.append({
                'title': article.get('title', ''),
                'score': score,
                'published': article.get('published', '')
            })

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
