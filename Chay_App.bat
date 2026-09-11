@echo off
if "%~1"==":mo_chrome_only" goto mo_chrome_only
set PYTHONIOENCODING=utf-8
chcp 65001 > nul
echo ============================================
echo   VN Invest Dashboard
echo ============================================
echo.

REM Kích hoạt venv nếu có
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Kiểm tra streamlit
python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo Chua co streamlit. Dang cai dat...
    pip install -r requirements.txt
)

REM Chrome dat lenh tu dong (tab Phai Sinh) — chi mo neu chua co cong 9222
REM dang lang nghe, tranh mo trung cua so Chrome cung profile.
netstat -ano | findstr /R /C:":9222 .*LISTENING" >nul
if errorlevel 1 (
    echo Dang mo Chrome dat lenh tu dong...
    start "" "%~dp0Chay_Chrome_AutoTrade.bat"
) else (
    echo Chrome dat lenh tu dong da dang chay ^(cong 9222^).
)
echo.

REM Chan mo server Streamlit THU HAI: hai server cung chay worker doi soat
REM tranh khoa trang thai, lenh that bi bo (11/09/2026). Da co thi chi mo trinh duyet.
netstat -ano | findstr /R /C:":8501 .*LISTENING" >nul
if not errorlevel 1 (
    echo May chu Streamlit DA CHAY tren cong 8501 - KHONG mo server thu hai.
    echo Mo trinh duyet toi server dang chay...
    call :mo_chrome
    pause
    exit /b 0
)
echo Dang khoi dong dashboard...
echo Truy cap: http://localhost:8501
echo.
REM Mo app bang CHROME cua so rieng (khong dung trinh duyet mac dinh Edge, de
REM dong nham cung tab khac). --server.headless tat tu mo trinh duyet cua
REM Streamlit; cua so phu cho 6 giay cho server len roi moi goi Chrome.
start "" /min cmd /c "ping -n 7 127.0.0.1 >nul & call "%~f0" :mo_chrome_only"
streamlit run app.py --server.headless true
pause
exit /b 0

:mo_chrome_only
call :mo_chrome
exit /b 0

:mo_chrome
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "%CHROME%" (
    start "" "%CHROME%" --new-window http://localhost:8501
) else (
    echo Khong thay Chrome tai "%CHROME%" - dung trinh duyet mac dinh.
    start "" http://localhost:8501
)
exit /b 0
