@echo off
title NaturalGasTrader A.I. -- Natural Gas Spread Betting
color 0A

echo Cleaning up any existing GasTrader processes...
taskkill /F /FI "WINDOWTITLE eq GasTrader A.I. -- Dashboard*" /T > nul 2>&1
taskkill /F /FI "WINDOWTITLE eq GasTrader A.I. -- Engine*" /T > nul 2>&1
timeout /t 2 /nobreak > nul

echo.
echo ============================================================
echo   GasTrader A.I. -- Natural Gas Spread Betting
echo   Starting all systems...
echo ============================================================
echo.

cd /d %~dp0

echo [1/3] Starting Dashboard server in new window...
start "GasTrader A.I. -- Dashboard" cmd /c "cd /d %~dp0 && echo. && echo ============================================================ && echo   GasTrader A.I. -- Dashboard && echo   Port 5006  --  http://localhost:5006 && echo ============================================================ && echo. && python dashboard_gas.py"

echo [2/3] Starting Engine (Watchdog) in new window...
start "GasTrader A.I. -- Engine" cmd /c "cd /d %~dp0 && echo. && echo ============================================================ && echo   GasTrader A.I. -- Engine (Galahad Watchdog) && echo   Natural Gas Spread Betting -- Paper Trading Mode && echo   Watchdog manages main_gastrader.py automatically && echo   Press Ctrl+C here to stop the engine safely && echo ============================================================ && echo. && python watchdog_gas.py"

echo [3/3] Waiting 5 seconds then opening browser...
timeout /t 5 /nobreak > nul

start http://localhost:5006

echo.
echo ============================================================
echo   GasTrader A.I. is running.
echo.
echo   Dashboard:  http://localhost:5006
echo   Engine:     Watchdog managing main_gastrader.py
echo   Mode:       PAPER TRADING (PAPER_TRADING_MODE=True)
echo.
echo   To stop: Close the Engine window or press Ctrl+C in it.
echo   Logs:    logs\gastrader.log
echo            logs\watchdog_gas.log
echo            logs\gas_trades.csv
echo ============================================================
echo.
