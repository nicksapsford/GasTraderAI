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
