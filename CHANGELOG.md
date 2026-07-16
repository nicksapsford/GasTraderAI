## [1.1.6] - 2026-07-16
### Added
- Job 1 (Gaius Commission 001, Priority 1): indicator snapshot at signal time in
  phantom_trades.csv. 17 columns APPENDED to the right of the existing 14-col schema
  (existing positions unchanged): ssl_daily/1hr/5min, rsi_daily/1hr/5min,
  tmo_1hr/5min, macd_1hr/5min, chande_mo_1hr/5min, money_flow_1hr/5min, morgan_score,
  session, guinevere_score. Captured from values Merlin already fetched for Arthur
  (no new data fetch) via phantom_tracker.build_snapshot() -> record_decision(indicators=).
  The snapshot build is wrapped in its own try/except so a failure can never stop a
  phantom row being written. phantom_tracker now migrates an older 14-col file in place
  on first use (old rows keep positions; new columns blank). Chronicle & Gaius read by
  column name and are unaffected. (guinevere_score currently blank pending a safe cached
  source -- column reserved.)

## [1.1.5] - 2026-07-14
### Fixed
- Morgan confidence (perf.confidence_score) now included in the lightweight always-running
  dashboard push (_push_dashboard_live), so /api/state exposes it in ALL market states --
  including the 21:00-22:00 UTC break. Previously perf was only pushed on full candle ticks
  (skipped when the market is closed), so RoundTable / Gaius / Chronicle showed null
  confidence out of hours. Matches CryptoTrader (performance in every push).

## [1.1.4] - 2026-07-13
### Fixed
- Bug B: open-position floating (unrealised) P&L now computed and exposed to RoundTable (`unrealised_gbp`, spread-inclusive).
- Bug C: "Locked P&L" now only shows once the trailing stop trails to break-even (genuine secured profit); until then "---".

## [1.1.3] - 2026-07-12
### Fixed
- Log timestamps now emitted in UTC (logging.Formatter.converter = time.gmtime; datefmt suffixed " UTC") across main, watchdog and dashboard. Previously local/BST, causing a +1h mismatch vs the UTC CSV artefacts (phantom_trades.csv etc.).
### Added
- ALBION STANDING RULE comment blocks baked into the logging setup and the log/analysis modules (phantom_tracker.py, performance_gas.py, dashboard stay-out reader): all timestamps are UTC, never BST/local.

## [1.1.2] - 2026-07-11
### Added
- Silent launcher (pythonw -- no console windows); output to logs/console.log with daily rotation (7 days kept)
- Launcher now starts the dashboard + watchdog silently (was cmd windows)

## [1.1.1] - 2026-07-11
### Added
- Morgan confidence persistence: CSV audit trail in logs/morgan_confidence.csv
  (save_confidence/load_confidence; set_confidence now appends a row after the
  JSON persist).
- Guinevere sentiment persistence: CSV audit trail in logs/guinevere_sentiment.csv
  (save_sentiment; fetch_gas_sentiment records sentiment/score/top-3 headlines and
  the EIA window flag each fetch).
- Morgan confidence restored on restart: startup hook in main_gastrader.py reads
  the last CSV row and re-applies it (else logs baseline 50).

## [1.1.0] - 2026-07-11
### Added
- Morgan individual phantom feedback: persistent confidence store in
  logs/morgan_confidence.json (get_confidence/set_confidence, _morgan_lock).
- apply_phantom_verdict_feedback(): per-verdict confidence adjustment
  (NEUTRAL 0.0; CORRECT +raw / WRONG -raw; raw=clamp(abs(pnl)/50, 0.5, 2.0)).
- process_new_phantom_verdicts(): daemon poller (MorganPhantomPoller, 300s)
  applying individual feedback to unprocessed CORRECT/WRONG verdicts and
  marking them processed (no double-counting).
- Reported confidence now folds in the Morgan phantom delta
  (score + (get_confidence() - 50)), distinct from the stay-out quality nudge.
- Startup hook in main_gastrader.py launching the phantom verdict poller.
### Audit
- Arthur prompt (agent_brain_gas.py): audited for hardcoded win-rate/backtest/
  historical figures. CLEAN — none present; no reset required.

## [1.0.1] - 2026-07-11
### Added
- 7 flat fields to /api/state for the RoundTable overview: lancelot_status,
  lancelot_fails, lancelot_fail_reasons, arthur_decision, arthur_confidence,
  arthur_consulted, locked_pnl (derived defensively; /api/state never 500s).
### Fixed
- Compact Open Position panel (Entry/Stop/Target): fixed-width label column with
  the value immediately after, replacing the wide label-left/value-hard-right layout.

## [1.0.0] - 2026-07-10
### Added
- Initial release — NaturalGasTrader A.I.
- Cloned from OilTrader A.I. v1.0.2
- Natural Gas (NATURALGAS) on Capital.com
- Guinevere News module (Currents API)
- Gas-specific news keywords
- EIA Gas Storage report calendar integration
- Phantom P&L tracker (inherited from OilTrader)
- Morgan STAY OUT quality integration
- Capital.com startup stagger: 60s delay
