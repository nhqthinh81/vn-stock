@echo off
REM Khoi dong Alert Watcher — theo doi scan_result.csv, tu gui Telegram
REM Dat shortcut file nay vao shell:startup de tu chay cung Windows
cd /d "%~dp0"
"C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe" -X utf8 alert_watcher.py
pause
