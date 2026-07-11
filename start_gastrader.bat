@echo off
REM Albion Trading Desk -- silent launcher (pythonw, no windows) + log rotation
cd /d C:\Users\abc\Desktop$dir
if not exist logs mkdir logs
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set "dated=%%c%%b%%a"
if exist logs\console.log (
    if exist "logs\console_%dated%.log" del "logs\console_%dated%.log"
    rename logs\console.log "console_%dated%.log"
)
forfiles /p logs /m console_*.log /d -7 /c "cmd /c del @path" 2>nul
start /B "" pythonw dashboard_gas.py >> logs\console.log 2>&1
start /B "" pythonw watchdog_gas.py >> logs\console.log 2>&1
