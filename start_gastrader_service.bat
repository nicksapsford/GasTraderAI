@echo off
title GasTrader A.I. -- Service Mode
cd /d %~dp0

echo Starting GasTrader A.I. in service mode (Task Scheduler)...

echo Cleaning up any existing GasTrader processes...
powershell -NoProfile -Command "Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like '*dashboard_gas.py*' -or $_.CommandLine -like '*watchdog_gas.py*' -or $_.CommandLine -like '*main_gastrader.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" > nul 2>&1
ping -n 3 127.0.0.1 > nul

start /B python dashboard_gas.py

ping -n 11 127.0.0.1 > nul

start /B python watchdog_gas.py

echo GasTrader A.I. launched in background -- dashboard + watchdog running.
exit /b 0
