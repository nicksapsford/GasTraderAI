## [1.2.0] - 2026-07-16
### Changed
- FULL recalibration to NatGas's real ~$2.89/MMBtu scale (Nick & Archie sign-off, 60-day
  NG=F backtest). Prior values were inherited from a larger-instrument scale.
  * strategy_gas.py: TRAILING_STOP_POINTS 60 -> 0.15 ($/MMBtu, ~1.2x median daily range;
    the old $60 stop sat at -$57/+$63, UNREACHABLE -- no real protection).
    TAKE_PROFIT_POINTS 300 -> 0.75 (5:1 safety ceiling). SPREAD_POINTS 0.3 -> 0.005
    (confirmed Capital.com demo).
  * phantom_tracker.py: VERDICT_THRESHOLD 10 -> 0.06 (40% of the 0.15 stop). NEW verdicts
    only; existing NEUTRAL rows unchanged.
  * agent_brain_gas.py: Arthur prompt updated to new stop/target/spread + a POINT
    CONVENTION statement (1 point = $1/MMBtu, never x100); position display :+.1f -> :+.3f;
    self-test price 4160 -> 2.90.
  * REMINDERS.txt: price ~$4,155 -> ~$2.89; stop/target/spread/size corrected.
  * paper_trader_gas.py: self-test prices ~4155 -> ~2.88 scale.
### Risk note
- GasTrader had no effective hard stop since launch (unreachable 60-pt stop; the 20:45
  force-close was the de-facto exit). This restores real stop protection (loss capped
  ~£20/trade). Position size now scales inversely with the stop -> ~178 oz, ~400x larger
  than before. Review the first live sessions closely.
## [1.1.7] - 2026-07-16
### Fixed
- Job 3 (Gaius Commission 001, Priority 3): GasTrader had no morgan_confidence.csv, so
  Morgan read the 50/MEDIUM fallback and could not persist. Root cause: the file is
  only written by set_confidence(), which fires on a CORRECT/WRONG phantom verdict or
  on startup-restore when a value already exists -- GasTrader had only NEUTRAL phantom
  verdicts and no prior file, so neither path ever ran. The startup restore's
  no-saved-value branch only logged the baseline without writing. Fix: initialise
  morgan_confidence.csv at the baseline on startup when none exists
  (set_confidence(get_confidence(), reason='init')). The Morgan module itself was
  correct and active; the phantom-verdict feedback loop is intact and will adjust the
  score once non-NEUTRAL verdicts arrive.

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
