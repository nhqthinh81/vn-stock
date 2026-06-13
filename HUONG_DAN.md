# Hướng dẫn sử dụng VN Invest Dashboard

## Bước 1: Cài đặt môi trường

```bash
# Tạo virtual environment
python -m venv .venv

# Kích hoạt (Windows)
.venv\Scripts\activate

# Kích hoạt (Mac/Linux)
source .venv/bin/activate

# Cài thư viện
pip install -r requirements.txt
```

## Bước 2: Chạy ứng dụng

**Windows**: Double-click `Chay_App.bat`

**Mac/Linux**:
```bash
chmod +x run_app.sh
./run_app.sh
```

**Thủ công**:
```bash
streamlit run app.py
```

Truy cập: **http://localhost:8501**

---

## Hướng dẫn từng tab

### Tab Cơ Bản
1. Nhập mã cổ phiếu vào sidebar (ví dụ: `HPG`, `VNM`, `ACB`)
2. Xem chỉ số tài chính: P/E, P/B, ROE, EPS...
3. Đọc phần **Nhận định tự động** để có góc nhìn nhanh về định giá, sinh lời, đòn bẩy, thanh khoản

### Tab Kỹ Thuật
1. Xem giá hiện tại + tín hiệu (BUY-A / BUY-B / HOLD / SELL-B / SELL-A)
2. Xem biểu đồ giá + SMA20/50
3. Xem biểu đồ RSI và MACD Histogram
4. Bảng 10 phiên gần nhất

### Tab Quick Scan
1. Nhấn **Scan watchlist đầy đủ** để quét toàn bộ mã (~10–30s tùy số lượng)
2. Nhấn **Làm mới giá** để cập nhật giá nhanh mà không tính lại signal
3. Dùng bộ lọc để xem mã theo tín hiệu / rủi ro / giai đoạn
4. Click vào mã bất kỳ trong bảng để xem chi tiết

### Tab Danh Mục
1. Upload file CSV theo định dạng:
   ```
   symbol,quantity,avg_price,sector
   HPG,1000,25000,Thép
   VNM,500,70000,Tiêu dùng
   ```
2. Hoặc tích chọn **Dùng file mẫu** để xem demo với `portfolio_mau.csv`
3. Xem tổng hợp: giá vốn, giá trị thị trường, lãi/lỗ
4. Xem phân bổ danh mục theo ngành

---

## CLI (nâng cao)

```bash
# Scan 1 mã
python -m vn_invest scan HPG

# Scan nhiều mã
python -m vn_invest scan HPG ACB VNM FPT

# Scan toàn bộ watchlist
python -m vn_invest scan

# Xem danh sách từ cache
python -m vn_invest list

# Lọc theo tín hiệu
python -m vn_invest list --signal BUY-A

# Lọc theo rủi ro
python -m vn_invest list --risk Low
```

---

## Giải thích tín hiệu

| Tín hiệu | Điểm KT | Ý nghĩa |
|-----------|---------|----------|
| BUY-A  | ≥ 75 | Tín hiệu mua mạnh |
| BUY-B  | 55–74 | Tín hiệu mua |
| HOLD   | 35–54 | Giữ nguyên |
| SELL-B | 25–34 | Tín hiệu bán |
| SELL-A | < 25  | Tín hiệu bán mạnh |

| Giai đoạn | Đặc điểm |
|-----------|----------|
| Accumulation | Tích lũy — giá thấp, RSI thấp |
| Markup | Tăng trưởng — giá trên EMA34, RSI > 50 |
| Distribution | Phân phối — giá cao, RSI > 60 |
| Markdown | Giảm mạnh — giá thấp, RSI < 40 |
| Neutral | Trung lập |

---

## Thêm mã vào watchlist

Mở `vn_invest/config.py`, chỉnh `DEFAULT_WATCHLIST`:

```python
DEFAULT_WATCHLIST = [
    "VNM", "HPG", "ACB", "VCB",  # thêm mã vào đây
    "YOUR_SYMBOL",
]
```

---

## Lỗi thường gặp

**Lỗi: No module named 'vnstock'**
```bash
pip install vnstock
```

**Lỗi: Port 8501 đã bị dùng**
```bash
streamlit run app.py --server.port 8502
```

**Dữ liệu không tải được**
- Kiểm tra kết nối internet
- Thử đổi nguồn dữ liệu từ KBS sang VCI trong sidebar
- Một số mã nhỏ có thể không có đủ dữ liệu
