@echo off
rem Kiem tra nhanh cong debug 9222 co dang mo dung trang SmartPro khong.
rem Chay truoc moi phien giao dich — tranh lap lai su co 08/09/2026
rem (Chrome giu cong 9222 nhung o SmartOne, khong ai de y suot 10 ngay).

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Kiem_Tra_Chrome_AutoTrade.ps1"
echo.
pause
