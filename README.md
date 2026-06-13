# VN Invest Dashboard

Dashboard phân tích chứng khoán Việt Nam — Streamlit + vnstock.

## Tính năng
- **Cơ Bản**: Chỉ số tài chính (P/E, P/B, ROE, EPS) + nhận định tự động
- **Kỹ Thuật**: RSI, MACD, EMA34, tín hiệu BUY/SELL/HOLD, rủi ro, giai đoạn
- **Quick Scan**: Quét toàn bộ watchlist, lọc theo tín hiệu
- **Danh Mục**: Upload CSV → tính lãi/lỗ, phân bổ ngành

## Cài đặt nhanh

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Yêu cầu
- Python 3.10+
- Không cần API key (dùng vnstock nguồn mở)

Xem [HUONG_DAN.md](HUONG_DAN.md) để biết thêm chi tiết.
