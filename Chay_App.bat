@echo off
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

echo Dang khoi dong dashboard...
echo Truy cap: http://localhost:8501
echo.
streamlit run app.py
pause
