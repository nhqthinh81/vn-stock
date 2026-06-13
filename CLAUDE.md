# VN Invest App — CLAUDE.md

## Dự án là gì
Dashboard phân tích chứng khoán Việt Nam: Streamlit frontend + vn_invest Python package.
- Nguồn dữ liệu giá/kỹ thuật: Amibroker (`C:\AmibrokerData\history_by_ticker\`)
- Nguồn dữ liệu tài chính cơ bản: vnstock (KBS/VCI)
- AI Signal: LSTM model tự train từ Amibroker data

## Cách chạy
```powershell
streamlit run "g:\Other computers\My Computer\BHDN\DHKD\Linhtinh\antigravity2\vn-invest-app\app.py"
# Hoặc double-click Chay_App.bat
# Truy cập: http://localhost:8501
```

## LSTM — Train & Inference
```powershell
# Train từ đầu (từ vn-invest-app directory):
python -m vn_invest.train_lstm --mode train

# Fine-tune model hiện có:
python -m vn_invest.train_lstm --mode finetune

# Model paths:
# v7 (10 features, ưu tiên): C:\AmibrokerData\stock_lstm_v7.keras
# v6 (5 features, fallback):  C:\AmibrokerData\stock_lstm_v6_multi.keras
# Scaler v7:                  C:\AmibrokerData\stock_scaler_v7.pkl
```

## Cấu trúc
```
vn-invest-app/
├── app.py                   # Streamlit dashboard (5 tabs)
├── vn_invest/
│   ├── config.py            # Ngưỡng signal, DEFAULT_WATCHLIST (20 mã)
│   ├── data.py              # Fetch vnstock: giá, ratios lịch sử, company
│   ├── indicators.py        # RSI, MACD, EMA, tech_score, signal/risk/phase
│   ├── screener.py          # Scan Amibroker data, cache JSON, filter
│   ├── lstm.py              # LSTM inference (v6/v7 auto-select)
│   ├── train_lstm.py        # Training pipeline (10 features, 3 heads T+5/T+10/T+25)
│   ├── alerter.py           # Composite score, spam filter, Telegram alerts
│   ├── portfolio.py         # CSV upload, PnL, sector allocation
│   └── cli.py               # CLI: scan + list
├── data/
│   ├── scores_cache.json    # Cache scan (tự tạo)
│   ├── alert_history.json   # Lịch sử cảnh báo Telegram (spam filter)
│   ├── model_metrics.json   # Metrics lần train gần nhất
│   └── train_running.log    # Log training đang chạy
├── .env                     # API keys (gitignored)
├── .env.example
├── requirements.txt
└── Chay_App.bat
```

## 5 Tabs

| Tab | Nội dung |
|-----|----------|
| Cơ Bản | Chỉ số tài chính theo Năm/Quý, biểu đồ xu hướng 6 nhóm, nhận định tự động |
| Kỹ Thuật | Giá Amibroker + SMA/RSI/MACD + AI Score LSTM (T+5/T+10/T+25) |
| Quick Scan | Scan 263 mã từ Amibroker Explorer, Khuyến Nghị Nhanh, filter AI Score + Đồng thuận |
| Danh Mục | Upload CSV → tính PnL, phân bổ ngành |
| Model AI | Quản lý LSTM, auto-retrain, gửi cảnh báo Telegram |

## Nguồn dữ liệu — Phân cấp

### Amibroker (ưu tiên cho kỹ thuật & LSTM)
```
C:\AmibrokerData\
├── history_by_ticker\      # 440 file CSV — nguồn chính cho scan + LSTM
├── History_DB\             # 390 file CSV — cũ hơn
└── scan_result.csv         # Output Amibroker Explorer (263 mã, có Rec + Score)
```

**Format Amibroker CSV date**: `01YYMMDD` → parse bằng `_parse_ami_date()` trong `lstm.py`/`screener.py`

**scan_result.csv columns**: `Ticker,Date,Close,Vol,PctChange,Vol10,Rec,Score,...`
- `Rec`: 1-5 (chất lượng tín hiệu)
- `Score`: 0-100
- ⚠️ Số Vol có dấu phẩy ngàn → parse từng dòng lấy field đầu, không dùng `pd.read_csv` thẳng

### vnstock (chỉ dùng cho tab Cơ Bản)
- KBS: hỗ trợ `finance.ratio(period="annual")` nhưng thực tế trả ~4 quý gần nhất
- VCI: `finance.ratio(period="annual")` trả dữ liệu rác (toàn nhãn '2018')
- VCI: `finance.ratio(period="quarter")` chỉ trả 4 kỳ (2018-Q1..Q4)
- **Thực tế**: dùng KBS cho mọi trường hợp, filter Q4 khi user chọn "Năm"

## Signal Classification

### Tech Score (indicators.py)
```python
tech_score = calculate_tech_score(rsi, macd_hist, dist_ema34_pct)  # 0-100
signal     = classify_signal(tech_score)   # BUY-A/BUY-B/HOLD/SELL-B/SELL-A
risk       = classify_risk(tech_score, dist_ema34_pct)
phase      = classify_phase(rsi, dist_ema34_pct)
# Phases: Accumulation / Markup / Distribution / Markdown / Neutral
```

### AI Score — LSTM (lstm.py)
```python
# 10 features: RSI, MACD_hist, Dist_EMA, Log_Ret, Vol_Change,
#              ATR_norm, RSI_slope5, Vol_ratio20, EMA_trend, BB_pos
# 3 output heads: T+5, T+10, T+25 (binary classification)
ai_score = (conf_t5*0.25 + conf_t10*0.35 + conf_t25*0.40) * 100

# Signal thresholds (calibrated từ backtest v6):
# BUY-A: >= 50 | BUY-B: >= 40 | HOLD: >= 30 | SELL-B: >= 20 | SELL-A: < 20
```

### Composite Score — Alerter (alerter.py)
```python
composite = ami_score * 0.40 + lstm_score * 0.40 + tech_score * 0.20
# Alert threshold: BUY >= 65, SELL <= 35
# Spam filter: cooldown 3 ngày / symbol+signal
```

### Đồng thuận (Consensus) — Quick Scan
| AI Score | Điểm KT | Nhãn |
|---|---|---|
| ≥ 70 + KT ≥ 60 | ✅ Mua mạnh |
| ≥ 50 + KT ≥ 50 | 🟢 Tích cực |
| ≤ 30 + KT ≤ 35 | 🔴 Bán |
| ≤ 40 + KT ≤ 45 | 🟠 Thận trọng |
| ≥ 70 + KT < 50 | ⚠️ AI↑ KT↓ (mâu thuẫn) |
| < 40 + KT ≥ 60 | ⚠️ AI↓ KT↑ (mâu thuẫn) |

## item_id thực tế vnstock
| Chỉ số | item_id |
|--------|---------|
| P/E | `p_e` |
| P/B | `p_b` |
| ROE | `roe` |
| ROA | `roa` |
| EPS | `trailing_eps` |
| Biên lãi gộp | `gross_profit_margin` |
| Biên lãi ròng | `net_profit_margin` |
| Nợ/VCSH | `debt_to_equity` |
| Nợ/TS | `debt_to_assets` |
| Thanh toán nhanh | `quick_ratio` |
| Thanh toán ngắn hạn | `short_term_ratio` |
| Trả lãi vay | `interest_coverage` |
> ⚠️ Sai hay gặp: `pe_ratio`, `pb_ratio`, `net_margin` — SAI hết.

## AI Analysis — analyzer.py

### Quy tắc gọi Claude API
```python
# KHÔNG dùng anthropic SDK — lỗi TypedDict Python 3.13
# Dùng httpx.post() trực tiếp:
resp = httpx.post("https://api.anthropic.com/v1/messages", ...)
_timeout = 300 if "sonnet" in model else 120  # Sonnet cần 300s cho 8192 tokens
# Luôn kiểm tra stop_reason:
if data.get("stop_reason") == "max_tokens":
    text += "\n\n⚠️ _Phân tích bị cắt do giới hạn token._"
```

### Models
- **Haiku** (`claude-haiku-4-5-20251001`): phân tích BCTC lần đầu, `max_tokens=8192`
- **Sonnet** (`claude-sonnet-4-6`): phản biện + chatbot, `timeout=300`, `max_tokens=4096`

### 3 lớp AI trong Tab Cơ Bản
1. **Phân tích AI chuyên sâu** (nút bấm) — Haiku + context: BCTC, cổ đông, giá 10 phiên, RSS news
2. **Phản biện** (nút bấm, sau khi có phân tích) — Sonnet, max 600 từ, prompt cứng
3. **Chat follow-up** (`st.chat_input`) — Sonnet, 6 turn history, raw BCTC data in context

### Tab Kỹ Thuật — Chat AI
- `_render_chatbot("tech", ...)` với context: RSI/MACD/EMA/Signal + 10 phiên

### Hàm helper chatbot
```python
# PHẢI định nghĩa TRƯỚC with tab_basic: (không thể define sau khi gọi)
def _build_basic_context(symbol, overview, fs_periods, fs_income, fs_balance, fs_cashflow, shareholders) -> str
def _build_tech_context(symbol, sig, hist) -> str
def _render_chatbot(tab_key, symbol, system_context, placeholder) -> None
```

### _call_claude() — signature đầy đủ
```python
def _call_claude(prompt, model, max_tokens, system=None, messages=None) -> str:
    # system → Anthropic top-level system parameter (context/data)
    # messages → multi-turn [{role, content}] — dùng cho chatbot
    # Nếu messages=None → tự build từ prompt (1 user message)
```

### Chatbot — dùng system param + multi-turn messages
```python
# SAI: nhét toàn bộ context vào 1 user message (Claude bỏ qua dữ liệu)
full_prompt = system_context + "\n\nUser: " + user_q  # ← SAI

# ĐÚNG: system param riêng + messages đúng cấu trúc
_call_claude(prompt="", model=..., system=sys_prompt, messages=[
    {"role": "user", "content": turn_q},
    {"role": "assistant", "content": turn_a},
    {"role": "user", "content": user_q},
])
```

### _build_tech_context — key names đúng từ get_latest_signals()
```python
# get_latest_signals() trả: close, rsi, macd_hist, dist_ema34_pct,
#                           log_return, tech_score, signal, risk, phase
# KHÔNG có: signal_class, risk_level, dist_ema_pct, ema34
sig.get("signal")         # ✓  (không phải "signal_class")
sig.get("risk")           # ✓  (không phải "risk_level")
sig.get("dist_ema34_pct") # ✓  (không phải "dist_ema_pct")
```

### _tech_hist — lấy date từ cột "time", không dùng df.index
```python
# SAI: df_price.index là RangeIndex (0,1,2...) sau reset_index(drop=True)
"periods": [str(r)[:10] for r in df_price.index[-10:]]  # ← cho ra "50","51"...

# ĐÚNG: dùng cột time
_df10 = df_price.tail(10)
"periods": [str(t)[:10] for t in _df10["time"]]
```

### fs_periods phải khởi tạo trước if _fs_loaded:
```python
# Khai báo default TRƯỚC block điều kiện — chatbot gọi ở ngoài block này
fs_periods  = []
fs_income   = {}
fs_balance  = {}
fs_cashflow = {}
if _fs_loaded:
    fs = _fetch_statements(...)
    fs_periods = fs.get("periods", [])
    ...
```

## item_id BCTC thực tế (VCI)

| Nhóm | item_id | Ý nghĩa |
|------|---------|---------|
| KQKD | `isa1`, `isa3`, `isa5`, `isa20` | DT, DT thuần, LN gộp, LNST |
| CĐKT | `bsa53`, `bsa54`, `bsa55`, `bsa56`, `bsa71`, `bsa78` | Tổng TS, Nợ, Nợ NH, Vay NH, Vay DH, VCSH |
| CĐKT | `bsa9`, `bsa15`, `bsa57` | Phải thu KH, HTK, Phải trả NB (cho CCC/DSO/DPO) |
| LCTT | `cfa18`, `cfa26`, `cfa34`, `cfa29`, `cfa30` | CFO, CFI, CFF, Vay mới, Trả nợ |

## Dữ liệu — Quy tắc quan trọng

- **charter_capital** từ KBS: đã là **tỷ đồng** → hiển thị thẳng, KHÔNG chia thêm
- **isa8** (chi phí lãi vay): số âm → `icr = ebit / abs(int0)`
- **Events cảnh báo**: chỉ xét trong **180 ngày gần nhất** (6 tháng) — tránh false positive
- **get_price_history**: signature `(symbol, days=int)` — KHÔNG có `period=`

## RSS News (news_fetcher.py)

- VN: VnEconomy chứng khoán/kinh tế, VnExpress kinh doanh
- INT: SCMP, Bloomberg Markets, Financial Times
- `search_market_news(symbol, company_name, sector, max_results=15)` → scored articles
- `detect_conflicts(articles)` → list mâu thuẫn VN vs quốc tế
- Cache 10 phút; lỗi RSS phải hiển thị rõ, không silent fail

## Lưu ý phát triển quan trọng

### Cache functions Streamlit
```python
# PHẢI định nghĩa @st.cache_data ở module-level (ngoài with tab_xxx:)
# Nếu định nghĩa bên trong block → Streamlit tạo key mới mỗi render → cache không hoạt động
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_ratio_hist(sym, period, n, src):   # key phải gồm tất cả tham số thay đổi
    return get_financial_ratios_history(...)
```

### vnstock banner encoding
```python
# Banner của vnstock gây UnicodeEncodeError trên Windows terminal (cp1252)
# Fix: suppress stdout khi init vnstock
def _get_stock(symbol, source):
    import io, sys
    _old_stdout, _old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = io.StringIO()
    try:
        obj = Vnstock().stock(symbol=symbol, source=source)
    finally:
        sys.stdout, sys.stderr = _old_stdout, _old_stderr
    return obj
```

### Amibroker CSV parsing
```python
# Date format: 01YYMMDD → "20YY-MM-DD"
def _parse_ami_date(date_val):
    s = str(int(date_val)).zfill(8)
    return f"20{s[2:4]}-{s[4:6]}-{s[6:8]}"

# scan_result.csv: dùng split(",")[0] để lấy ticker (số có dấu phẩy ngàn làm lệch cột)
# Không dùng pd.read_csv(usecols=[0]) — bị lỗi khi có số như 2,735,300
```

### scan_ami_watchlist vs scan_watchlist
- `scan_ami_watchlist(with_lstm=True)` — đọc Amibroker + LSTM, không cần vnstock, nhanh
- `scan_watchlist()` — gọi vnstock, bị rate limit, chậm → chỉ dùng fallback
- Quick Scan luôn dùng `scan_ami_watchlist`

### Khuyến Nghị Nhanh
- Đọc từ **full cache** (`load_cache()`) chứ không từ `df_scan` (đã filtered)
- Nếu đọc từ filtered → thay đổi khi user đổi filter → UX xấu

### Cảnh báo Telegram
- Cần `.env`: `TELEGRAM_TOKEN` và `TELEGRAM_CHAT_ID`
- Spam filter lưu tại `data/alert_history.json`
- Cooldown mặc định: 3 ngày / symbol+signal
- Dry run: preview kết quả trước khi gửi thật

### Training LSTM
- Phải chạy từ thư mục `vn-invest-app` (không phải thư mục con)
- Infinity values trong data: `np.where(np.isfinite(F), F, 0.0)` trong `build_sequences`
- Shape mismatch v6(5 feat) vs v7(10 feat): detect và auto switch sang full train
- Delay 3.1s/mã để tránh rate limit khi build dataset

## .env
```
VNSTOCK_API_KEY=...
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
AMIBROKER_HIST_DIR=C:\AmibrokerData\history_by_ticker
AMIBROKER_SCAN_CSV=C:\AmibrokerData\scan_result.csv
LSTM_MODEL_PATH=C:\AmibrokerData\stock_lstm_v7.keras
LSTM_SCALER_PATH=C:\AmibrokerData\stock_scaler_v7.pkl
ALERT_BUY_THRESHOLD=65
ALERT_SELL_THRESHOLD=35
ALERT_COOLDOWN_DAYS=3
```
