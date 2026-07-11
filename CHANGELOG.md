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
