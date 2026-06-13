#!/bin/bash
echo "============================================"
echo "  VN Invest Dashboard"
echo "============================================"

# Kích hoạt venv nếu có
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Kiểm tra streamlit
if ! python -c "import streamlit" 2>/dev/null; then
    echo "Chưa có streamlit. Đang cài đặt..."
    pip install -r requirements.txt
fi

echo "Đang khởi động dashboard..."
echo "Truy cập: http://localhost:8501"
streamlit run app.py
