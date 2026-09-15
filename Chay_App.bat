@echo off
if "%~1"==":mo_chrome_only" goto mo_chrome_only
cd /d "%~dp0"
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

REM Chon Python TUONG MINH: "python" trong PATH co the tro sang venv cua cong cu
REM khac (hermes-agent, khong co streamlit/pip - 15/09/2026).
set "PY=python"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)

REM width="stretch" can Streamlit 1.55 tro len; import thanh cong chua du.
"%PY%" -c "import streamlit, sys; sys.exit(tuple(map(int, streamlit.__version__.split('.')[:2])) < (1, 55))" 2>nul
if errorlevel 1 (
    echo Can Streamlit 1.55 tro len. Dang cap nhat thu vien...
    "%PY%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo Cai dat that bai. Chua khoi dong ung dung hoac Chrome AutoTrade.
        pause
        exit /b 1
    )
)

REM Chrome dat lenh tu dong (tab Phai Sinh) — chi mo neu chua co cong 9333
REM dang lang nghe, tranh mo trung cua so Chrome cung profile.
netstat -ano | findstr /R /C:":9333 .*LISTENING" >nul
if errorlevel 1 (
    echo Dang mo Chrome dat lenh tu dong...
    start "" "%~dp0Chay_Chrome_AutoTrade.bat"
) else (
    echo Chrome dat lenh tu dong da dang chay ^(cong 9333^).
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
"%PY%" -m streamlit run app.py --server.headless true
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
