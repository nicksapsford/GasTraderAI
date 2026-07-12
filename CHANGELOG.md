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
