@echo off
REM ============================================================================
REM  Alert Watcher — chay THU CONG (du phong).
REM
REM  Binh thuong KHONG can file nay: da co Task Scheduler "VNInvest_AlertWatcher"
REM  tu chay cung Windows moi lan dang nhap. Chi dung file nay khi muon chay tay
REM  va xem log truc tiep tren man hinh.
REM
REM  Watcher co khoa chong chay trung (mutex): neu task da chay san, tien trinh
REM  nay se tu thoat ngay thay vi gui canh bao trung.
REM
REM  Kiem tra task:  schtasks /query /tn VNInvest_AlertWatcher
REM  Go task:        schtasks /delete /tn VNInvest_AlertWatcher /f
REM ============================================================================
cd /d "%~dp0"
"C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe" -X utf8 alert_watcher.py
pause
