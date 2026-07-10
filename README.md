# NaturalGasTrader A.I. — Albion Trading Desk
**Version:** 1.0.0 | **Port:** 5006 | **Status:** Paper Trading

Part of the Albion Trading Desk — a multi-system AI paper
trading operation built by Nick, running on a dedicated
Dell Optiplex (Windows 11 Pro). NaturalGasTrader is the 6th
desk system.

**Market:** Natural Gas — Capital.com (NATURALGAS)
**Broker:** Capital.com demo (Z6CJSM, £1,000 virtual)
**Theme:** Forest Green #228B22

## The Team (Arthurian Naming)
| Role | Name | Function |
|------|------|----------|
| AI Brain | Arthur | Claude AI decision engine |
| Data Feed | Merlin | Natural Gas price + indicators |
| Pre-checks | Lancelot | Entry validation + EIA Gas Storage gate |
| Broker | Excalibur | Capital.com connector |
| Calendar | Guinevere | Economic calendar + Gas news sentiment |
| Performance | Morgan | P&L tracker + confidence |
| Watchdog | Galahad | Auto-restart |
| Notifier | Percival | Pushover alerts |
| Trader | Stanley | Paper trade execution |

## The Gas Edge — EIA Natural Gas Storage
The EIA Natural Gas Storage report releases **every Thursday
14:30 UTC** — the single biggest weekly catalyst for gas.
Lancelot **HARD BLOCKS** all entries **14:15–15:00 UTC on
Thursdays** (gas moves too violently to risk an entry), and
flags a caution for the post-report hour (15:00–16:00 UTC).

## Seasonal Patterns
- Winter (Oct–Mar): bullish-leaning on heating demand
- Summer: moderate (cooling demand)
- Spring/Autumn shoulder: range-bound

Natural gas has LOW correlation with equities (~0.1) and VERY
HIGH volatility — stops (60 pts) and targets (300 pts) are set
wide to accommodate typical intraday ranges.

## Guinevere News Module
Monitors real-time natural gas news via Currents API.
Bullish keywords: storage draw, cold snap, freeze, pipeline
disruption, LNG export, hurricane, polar vortex, heating demand.
Bearish keywords: storage build, mild weather, warm winter,
record storage, LNG glut, record production, shale boom.
Sentiment: BULLISH / BEARISH / NEUTRAL
Confidence adjustment: +8 / -8 / 0

## Phantom P&L Tracker
Records every STAY OUT decision with hindsight scoring.
Data saved to: logs/phantom_trades.csv

## Running
```
start_gastrader.bat
```
Dashboard: http://localhost:5006  (Forest Green theme)
Launches dashboard_gas.py + watchdog_gas.py (watchdog manages
main_gastrader.py). There is no app.py.

## API Key Setup
Add your Currents API key to .env:
  CURRENTS_API_KEY=your_key_here
Never commit .env to GitHub.
