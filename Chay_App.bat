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

echo Dang khoi dong dashboard...
echo Truy cap: http://localhost:8501
echo.
streamlit run app.py
pause
