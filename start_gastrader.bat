@echo off
title NaturalGasTrader A.I. - Port 5006
cd /d C:\Users\abc\Desktop\GasTraderAI
start /min "NaturalGasTrader A.I. Dashboard" cmd /c C:\Users\abc\AppData\Local\Programs\Python\Python313\python.exe dashboard_gas.py
start /min "NaturalGasTrader A.I. Engine" cmd /c C:\Users\abc\AppData\Local\Programs\Python\Python313\python.exe watchdog_gas.py
