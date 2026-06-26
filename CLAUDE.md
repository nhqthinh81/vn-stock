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
├── app.py                   # Streamlit dashboard (6 tabs)
├── vn_invest/
│   ├── config.py            # Ngưỡng signal, DEFAULT_WATCHLIST (20 mã)
│   ├── data.py              # Fetch vnstock: giá, ratios lịch sử, company
│   ├── indicators.py        # RSI, MACD, EMA, tech_score, signal/risk/phase
│   ├── screener.py          # Scan Amibroker data, cache JSON, filter
│   ├── lstm.py              # LSTM inference (v6/v7 auto-select)
│   ├── train_lstm.py        # Training pipeline (10 features, 3 heads T+5/T+10/T+25)
│   ├── phaisinh_tab.py      # Tab Phái Sinh: VN30F1M Signal Bot (Multi-TF + Trailing Stop)
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

## 6 Tabs

| Tab | Nội dung |
|-----|----------|
| Cơ Bản | Chỉ số tài chính theo Năm/Quý, biểu đồ xu hướng 6 nhóm, nhận định tự động |
| Kỹ Thuật | Giá Amibroker + SMA/RSI/MACD + AI Score LSTM (T+5/T+10/T+25) |
| Quick Scan | Scan mã từ Amibroker (263 đã lọc hoặc 440 tất cả), Khuyến Nghị Nhanh, filter, auto price-refresh |
| Danh Mục | Upload CSV → tính PnL, phân bổ ngành |
| Model AI | Quản lý LSTM, auto-retrain, gửi cảnh báo Telegram |
| Phái Sinh | VN30F1M Signal Bot: LSTM + Multi-TF trend + Trailing Stop, auto-refresh theo phiên |

## Nguồn dữ liệu — Phân cấp

### Amibroker (ưu tiên cho kỹ thuật & LSTM)
```
C:\AmibrokerData\
├── history_by_ticker\      # 386+ file CSV — nguồn chính cho scan + LSTM
├── History_DB\             # 390 file CSV — cũ hơn
└── scan_result.csv         # Output AFL auto-export sau mỗi lần Explore (~386 mã)
```

**Format Amibroker CSV date**: `01YYMMDD` → parse bằng `_parse_ami_date()` trong `lstm.py`/`screener.py`

**scan_result.csv columns** (format mới 8 cột):
`Ticker,Date,Close,Vol,Rec,Score,Setup,Forecast`
- `Rec`: số nguyên — {3: STRONG BUY, 2: ACCUMULATE, 1: WATCHING, -2: RISK SELL, -3: TOP SELL}
- `Score`: float 0-100 (có thể âm với SELL)
- `Setup`: GAP UP / PKT PIVOT / PULLBACK / PWR-PLAY / VCP TIGHT / FLAT BASE / --- (từ Mod_ID AFL)
- `Forecast`: BULL DIV / BEAR DIV / BB BOT REV / --- (từ For_ID AFL)
- Vol: dạng scientific notation `1.0113e+006` (không có dấy phẩy ngàn) → `split(",")` hoạt động đúng
- ⚠️ Chỉ `len(parts) >= 6` mới parse; dòng <6 cột bỏ qua

### vnstock (chỉ dùng cho tab Cơ Bản)
- KBS: hỗ trợ `finance.ratio(period="annual")` nhưng thực tế trả ~4 quý gần nhất
- VCI: `finance.ratio(period="annual")` trả dữ liệu rác (toàn nhãn '2018')
- VCI: `finance.ratio(period="quarter")` chỉ trả 4 kỳ (2018-Q1..Q4)
- **Thực tế**: dùng KBS cho mọi trường hợp, filter Q4 khi user chọn "Năm"

## Signal Classification

### Tech Score (indicators.py) — Signatures chuẩn
```python
# Phase phải tính TRƯỚC tech_score (phase là INPUT của tech_score)
phase      = classify_phase(rsi, dist_ema34_pct, price_trend_20d, ma_aligned)
tech_score = calculate_tech_score(
    rsi, macd_hist, dist_ema34_pct,
    ma_aligned=0, volume_ratio=nan, macd_bars_since_cross=999,
    phase="Neutral", weekly_macd_trend=0, rs_pct=nan
)
signal = classify_signal(tech_score, volume_ratio, rsi)  # rsi param quan trọng!
risk   = classify_risk(tech_score, dist_ema34_pct, atr_pct, bb_width_pct, volume_ratio)
```

### Tech Score — Công thức v2 (rebalanced cho VN market, session 8)
```
score = 50 (baseline)
  RSI Wilder:     ≤30 → +15 | ≥70 → -15 | else → (50-rsi)*15/20
  MACD freshness: hist>0: fresh≤3 → +15 | ≤10 → +10 | >10 → +5  (đổi dấu khi âm)
  MA Alignment:   ma_al * 8.0  → ±16  ← TRỌNG SỐ CAO NHẤT (dự báo tốt nhất VN)
  Dist EMA34:     -15%→-5% → +8 | -5%→+5% → +2 | cực → ±8
  Volume:         ≥1.5x → +5 | <0.6x → -5
  Wyckoff Phase:  Accum +6 | Markup +4 | Neutral 0 | Distrib -8 | Markdown -6
  Weekly MACD:    wmt * 8   → ±8  ← tăng từ ±6
  RS vs VNI 14d:  >5% → +8 | >0 → +4 | >-5% → -4 | ≤-5% → -8  ← tăng từ ±6
```
⚠️ **MACD dùng dấu + freshness**: freshness = `macd_bars_since_cross` column từ `add_all_indicators()`

### Ngưỡng signal (config.py)
```python
SCORE_BUY_A  = 70   # BUY-A: >= 70  (giảm từ 75 — đủ sample để thống kê)
SCORE_BUY_B  = 55   # BUY-B: 55-69
SCORE_SELL_B = 35   # SELL-B: 25-34
SCORE_SELL_A = 25   # SELL-A: <25
# HOLD: 35-54
```

### classify_signal() — Volume Gate VN + RSI Gate SELL-A
```python
def classify_signal(tech_score, volume_ratio=nan, rsi=nan):
    if tech_score >= SCORE_BUY_A:
        if volume_ratio > 4.0: return "BUY-B"   # FOMO >4x; 2-4x vẫn là breakout thật
        return "BUY-A"
    ...
    else:  # SELL-A zone
        if not isnan(rsi) and rsi < 40: return "SELL-B"  # oversold → VN bounce → không SELL-A
        return "SELL-A"
```
⚠️ **Volume gate VN ngược Mỹ**: hạ BUY-A khi volume >4x (FOMO), KHÔNG hạ khi volume thấp (tích lũy lặng lẽ).
⚠️ **RSI gate SELL-A**: SELL-A chỉ đáng tin khi RSI ≥40. RSI <40 + bad tech ở VN = oversold bounce, không phải downtrend.

### Indicators trong add_all_indicators()
```python
# Các cột chuẩn (session 6-8):
df["atr_pct"]              # ATR Wilder / close * 100
df["bb_upper/mid/lower/bb_width_pct"]  # Bollinger (1983)
df["volume_ratio"]         # volume / SMA20(volume)
df["ma_aligned"]           # -2/-1/0/+1/+2 (SMA20 vs SMA50 vs close)
df["price_trend_20d"]      # close.pct_change(20) * 100
df["macd_bars_since_cross"] # freshness: số bar kể từ MACD đổi dấu
df["weekly_macd_trend"]    # +1/-1/0 từ Elder Triple Screen (resample daily→weekly)
df["rs_14d"]               # stock return 14d - vni_ret_14d (inject từ ngoài trước khi gọi)
```
⚠️ `rs_14d` cần inject `vni_ret_14d` column TRƯỚC khi gọi `add_all_indicators(df)`:
```python
df["vni_ret_14d"] = df["Date"].map(vni_ret_series)
df = add_all_indicators(df)
```

## Backtest — vn_invest/backtester.py

### Chạy nhanh (không cần Streamlit)
```python
# run_backtest_quick.py tại root project
import sys; sys.path.insert(0, ".")
from vn_invest.backtester import run_backtest
r = run_backtest(forward_days=20, max_symbols=200)
print(r["buy_a_alpha"], r["market_avg_return"], r["signal_edge"])
```

### Metric đúng cho VN market
```python
"buy_a_alpha"      # BUY-A avg − market_avg  ← METRIC CHÍNH (>2% ở T+20 = tốt)
"signal_edge"      # BUY-A avg − SELL-A avg  ← reference (thường âm T+10, dương T+20)
"market_avg_return"  # avg return khi "mua bừa" bất kỳ mã
```
**Lý do:** SELL signals trong VN không dự báo giá xuống ngắn hạn. Cần Alpha vs market, không phải vs SELL-A.

### Kết quả đã đạt (session 8) — T+20, 200 mã
```
BUY-A Alpha: +2.43%  |  BUY-A win: 66.3%  |  BUY-A avg: +10.57%  |  n=798
BUY-B avg: +8.91%    |  Market avg: +8.14%
SELL-A avg: +3.39% (vẫn dương vì VN upward bias)  |  Signal Edge: +7.18%
```

### Filters đang hoạt động (backtester.py)
1. **signal_persistence_2d**: signal phải giữ ≥2 ngày liên tiếp mới tính
2. **macd_freshness**: fresh cross ≤3 bars = ±15; cũ >10 bars = ±5
3. **volume_gate_vn_adjusted(>4x)**: BUY-A → BUY-B khi volume >4x FOMO
4. **atr_filter(<5%)**: bỏ qua cổ phiếu atr_pct >5% (penny/thao túng)
5. **buya_hard_gate(MA+Weekly+Phase)**:
   - `ma_al < 1` → BUY-B (cần MA uptrend thật, không chỉ sideways)
   - `wmt == -1` → BUY-B (chỉ gate khi weekly âm RÕ RÀNG — wmt=0 no data vẫn cho qua)
   - phase Distribution/Markdown → BUY-B
   - `rs < -2` và có data → BUY-B
6. **market_regime**: skip BUY khi VNI bear; skip SELL khi VNI bull

⚠️ **wmt gate dùng `== -1` không phải `<= 0`**: wmt=0 (no weekly data) vẫn cho BUY-A; dùng `<= 0` sẽ block toàn bộ BUY-A trong Streamlit vì nhiều mã thiếu weekly data.

### HOLD win threshold (scale theo forward_days)
```python
hold_threshold = max(2.0, 2.0 * forward_days / 5)
# T+5: ±2% | T+10: ±4% | T+20: ±8%
```

### market_regime.py — 3 nguồn fallback
1. Amibroker local (5 tên file: VNI.csv, VNINDEX.csv, ^VNINDEX.csv, VNIDX.csv, VN-INDEX.csv)
2. Cache `data/vni_cache.csv` (TTL 24h)
3. vnstock API: `from vnstock import Quote` → `Quote("VNINDEX","VCI").history(...)`
⚠️ Dùng `Quote`, KHÔNG dùng `Stock` hay `Vnstock()` (UnicodeEncodeError banner)

### Classify Risk — đa chiều (indicators.py)
```
penalty += 2 nếu tech_score < 35  (xu hướng yếu/xuống)
penalty += 1 nếu tech_score < 55  (trung tính)
penalty += 2 nếu dist_ema > 12%   (quá xa EMA)
penalty += 1 nếu dist_ema > 6%
penalty += 2 nếu atr_pct > 4.5%   (biến động rất cao)
penalty += 1 nếu atr_pct > 3%
penalty += 1 nếu bb_width > 15%   (đang giãn mạnh)
penalty += 1 nếu bb_width < 5%    (squeeze)
penalty += 1 nếu signal BUY nhưng volume_ratio < 0.7
→ Low ≤1 | Medium ≤3 | High >3
```

### Phát hiện mẫu hình (indicators.py)
```python
detect_candle_patterns(df)  # 9 mẫu nến Nhật, Bulkowski stats
detect_chart_patterns(df)   # 7 mẫu hình giá (20-60 phiên), Bulkowski stats
detect_reversals(df)        # 6 tín hiệu đảo chiều (xem bên dưới)
build_reason(...)           # Sinh text lý do từ tất cả chỉ báo
```

### detect_reversals() — 6 tín hiệu đảo chiều
```python
# Trả: {"reversal_type": "bullish"|"bearish"|"none", "reversal_strength": 0-95, "reversal_signals": "..."}
# 1. RSI Divergence (Wilder 1978):    pivot window=8, khoảng cách ≥8 phiên, len≥30 bars
# 2. MACD Zero-Cross (Appel 1979):    fresh cross + 1-2 phiên xác nhận
# 3. BB Bounce (Bollinger 1983):      chạm band + đóng ngược chiều + RSI filter
# 4. Wyckoff Spring/Upthrust (1931):  range-bound ≤20% (PHẢI check) + volume spike + RSI
# 5. RSI Oversold/OB Exit (Elder):    RSI thoát <30 hoặc >70, 3 phiên liên tiếp đổi chiều
# 6. Volume Climax (Granville 1963):  volume >2.5x + giá spike + phiên tiếp reversal
#
# reversal_strength:
#   1 tín hiệu: avg_confidence × 0.80
#   2 tín hiệu: avg_confidence × 0.92
#   3+ tín hiệu: avg_confidence × 1.0  (max 95)
```
⚠️ **Wyckoff**: bắt buộc check `range_pct = (high20-low20)/low20 ≤ 0.20` — Spring/Upthrust chỉ có nghĩa khi giá đang tích lũy, không phải đang downtrend thẳng.
⚠️ **RSI Divergence**: `w=8` (không phải 4), `len≥30`. Pivot cách nhau <8 phiên = micro-pivot nhiễu.

### get_latest_signals() — cache fields đầy đủ
```python
# Bắt buộc:
close, rsi, macd_hist, dist_ema34_pct, log_return, tech_score, signal, risk, phase
candle_patterns, chart_patterns, reason   # string formatted
macd_rising, price_above_sma5_3d         # bool — xu hướng 3-5 phiên
reversal_type, reversal_strength, reversal_signals
# Conditional (chỉ có nếu không NaN):
atr_pct, bb_width_pct, volume_ratio
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

### Đồng thuận (Consensus) — _con_label() trong app.py
Ưu tiên từ cao xuống thấp:

**Không có AI Score:**
```
✅ Mua mạnh:  signal=BUY-A AND risk≠High AND phase∉{Distribution,Markdown} AND buy_ok
🟢 Tích cực:  signal∈{BUY-A,BUY-B} AND phase∉bad AND buy_ok
🔄 Đảo Chiều: reversal_type=bullish AND strength≥40 AND risk≠High AND NOT bear_block
🔴 Bán:       signal∈{SELL-A,SELL-B}
🟠 Thận trọng: risk=High OR bear_block OR reversal_type=bearish+strength≥40
🟡 Trung tính: còn lại
```

**buy_ok = MACD>0 AND rsi_ok AND trend_ok AND NOT bear_block**
- `rsi_ok`: RSI≤30 (oversold đảo chiều) OR RSI 41-69 (bình thường) — **loại RSI 31-40** (đang giảm chưa đảo) và RSI≥70 (overbought)
- `trend_ok`: macd_rising OR price_above_sma5_3d (ít nhất 1 trong 2)
- `bear_block`: có mẫu bearish mạnh (Three Black Crows, Evening Star...) OR dist < -20%
- `is_bear_rev` (bearish reversal mạnh) → "🟠 Thận trọng"

**Có AI Score:** AI score tham gia thêm điều kiện Mua mạnh/Tích cực/Bán/Thận trọng.

### KN Nhanh — 5 tabs
```
✅ Mua mạnh   | 🟢 Tích cực   | 🔄 Đảo Chiều (có caption cảnh báo rủi ro)
🔴 Bán        | ⚠️ Thận trọng
```
Sort: Mua mạnh/Tích cực/Đảo Chiều → nlargest(5, sort_col); Bán → nsmallest(5, sort_col)
`_reversal` sort by `reversal_strength` (không phải ai_score/tech_score)

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

### _call_claude() và _call_claude_stream() — signatures
```python
def _call_claude(prompt, model, max_tokens, system=None, messages=None) -> str:
    # Blocking call — dùng cho AI analysis (nút bấm), phản biện
    # system → Anthropic top-level system parameter (context/data)
    # messages → multi-turn [{role, content}] — dùng cho chatbot

def _call_claude_stream(prompt, model, max_tokens, system=None, messages=None):
    # Generator streaming SSE — PHẢI dùng cho chatbot (tránh treo UI)
    # Dùng với: ans = st.write_stream(_call_claude_stream(...))
    # st.write_stream trả về full string → lưu session_state bình thường
```

### Chatbot — BẮT BUỘC dùng streaming
```python
# SAI: httpx.post blocking → Streamlit timeout → treo UI
ans = _call_claude(..., model="claude-sonnet-4-6")  # ← TREO nếu >60s

# ĐÚNG: streaming hiện token ngay, không bao giờ treo
with st.chat_message("assistant"):
    ans = st.write_stream(_call_claude_stream(..., model="claude-sonnet-4-6"))
st.session_state[chat_key].append({"q": user_q, "a": ans})
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

### Period Normalization — _normalize_period() (data.py)
vnstock VCI dùng nhãn kỳ nội bộ: Q6=bán niên, Q9=9 tháng.
Hàm `_normalize_period(label)` là single source of truth cho mọi nơi xử lý kỳ:
```python
"2025-Q6"   → "H1/2025"   # bán niên — GIỮ LẠI (PTB và nhiều mid-cap chỉ báo cáo H1)
"2025-Q9"   → "9T/2025"   # 9 tháng — GIỮ LẠI
"2025-Q4_1" → "2025-Q4"   # bỏ suffix duplicate
"2026"      → None         # năm hiện tại — BỎ (chưa đủ dữ liệu)
"2025-Q7"   → None         # không hợp lệ — BỎ
"2025-Q1"   → "2025-Q1"   # giữ nguyên
```
KHÔNG dùng blacklist (dễ miss format lạ) — luôn dùng whitelist/normalize.
Dùng cho cả `get_financial_statements()` và `get_financial_ratios_history()`.

### AI Analysis — Quy tắc dữ liệu
```python
# AI analysis LUÔN tự fetch BCTC quarterly riêng — không dùng session state UI
_ai_fs = _fetch_statements(symbol, "quarterly")  # trong trigger block của AI

# Ngày trong prompt: PHẢI dùng datetime động
from datetime import date
_today_str = f"thang {date.today().month}/{date.today().year}"

# Trend block: tính QoQ % và cảnh báo "tăng DT giảm margin" tự động
# → Claude nhận cảnh báo sẵn, không bỏ sót xu hướng
```

## RSS News & Commodity Prices (news_fetcher.py)

### RSS Sources
- VN: VnEconomy chứng khoán/kinh tế, VnExpress kinh doanh, CafeF chứng khoán
- INT chung: Reuters Business, SCMP
- INT chuyên ngành: Mining.com (quặng/kim loại), SteelOrbis (thép toàn cầu)
- `search_market_news(symbol, company_name, sector, max_results=15)` → scored articles
- `detect_conflicts(articles)` → list mâu thuẫn VN vs quốc tế

### Commodity Prices
```python
get_commodity_prices(sector)  # → list[{label, value, date, source}]
# Ưu tiên: yfinance real-time (HRC=F, BZ=F, HG=F, NG=F, CT=F...)
# Fallback: FRED CSV API (iron ore, copper, mortgage rate...)
# 10 ngành: steel, oil gas, seafood, real estate, bank, retail, textile, timber, pharma, tech
# Cache 30 phút
```

### Macro Data (data.py)
```python
get_macro_data()  # IMF WEO datamapper — có estimate/forecast năm hiện tại
# Trả: {gdp_growth, cpi, current_acct} mỗi cái là list[{year, value, is_forecast}]
# + usdvnd_spot từ open.er-api.com
# Cache 6 tiếng
```

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

### AFL Export scan_result.csv — cơ chế
AFL file: `C:\Program Files (x86)\AmiBroker\Formulas\Custom\wycoff 24022026 11.afl`

Amibroker Explore KHÔNG tự export CSV — phải dùng `fopen/fputs` trong AFL:
```afl
if( Status("action") == actionExplore )
{
    Export_Path = "C:\\AmibrokerData\\scan_result.csv";
    i = BarCount - 1;
    if( Status("stocknum") == 0 )  // ticker đầu tiên → overwrite với header
    {
        fh = fopen( Export_Path, "w" );
        if(fh) { fputs( "Ticker,Date,Close,Vol,Rec,Score,Setup,Forecast\n", fh ); fclose(fh); }
    }
    fh = fopen( Export_Path, "a" );  // mỗi ticker → append 1 dòng
    if(fh)
    {
        Arr_D = Day(); Arr_M = Month(); Arr_Y = Year();  // PHẢI gán ra biến trước
        Line = StrFormat( "%s,%02.0f/%02.0f/%04.0f,%g,%g,%g,%g,",
            Name(), Arr_D[i], Arr_M[i], Arr_Y[i],
            Nz(C[i]), Nz(V[i]), Nz(Recommendation[i]), Nz(Score[i]) );
        Line = Line + WriteIf(Mod_ID[i]==600,"GAP UP",WriteIf(Mod_ID[i]==300,"PKT PIVOT",WriteIf(Mod_ID[i]==500,"PULLBACK",WriteIf(Mod_ID[i]==400,"PWR-PLAY",WriteIf(Mod_ID[i]==100,"VCP TIGHT",WriteIf(Mod_ID[i]==200,"FLAT BASE","---")))))) + ",";
        Line = Line + WriteIf(For_ID[i]==10,"BULL DIV",WriteIf(For_ID[i]==-10,"BEAR DIV",WriteIf(For_ID[i]==30,"BB BOT REV","---"))) + "\n";
        fputs( Line, fh );
        fclose( fh );  // BẮT BUỘC fclose() — thiếu → Error 53
    }
}
```

⚠️ **AFL fclose()**: PHẢI gọi `fclose(fh)` sau mỗi `fputs()` — thiếu → "Error 53: open file not closed"
⚠️ **AFL WriteIf với index**: `WriteIf(Mod_ID[i]==..., "text", ...)` hoạt động (trả scalar string) — không dùng biến trung gian vì biến AFL là array, không index được trong StrFormat
⚠️ **AFL syntax**: `Day()[i]` KHÔNG hợp lệ trong argument của function call → phải `Arr_D = Day(); Arr_D[i]`
⚠️ **AFL Guardian linter**: Sửa AFL file PHẢI qua Amibroker Formula Editor (`Ctrl+E`) — edit ngoài bị revert tự động

### Quick Scan — Cache Metadata Pattern
```python
# scan_cache.meta.json (sidecar của scan_cache.json):
# {"scanned_at": "2026-06-15 09:30:00", "count": 263, "price_refreshed_at": "2026-06-15 10:05:00"}
load_cache_meta()         # → dict với 3 fields trên
save_price_refresh_time() # cập nhật price_refreshed_at sau refresh_prices()
get_ami_scan_age()        # → "X phút trước" / "X giờ trước" / None

# Cảnh báo stale: nếu price_refreshed_at > scanned_at → signal cũ hơn giá
```

### Quick Scan — Hai Nguồn Mã
| Hàm | Nguồn | Số mã | Mục đích |
|-----|-------|-------|---------|
| `get_ami_watchlist()` | `scan_result.csv` | ~393 | Mã đã qua lọc Amibroker Explorer |
| `get_all_ami_symbols()` | `history_by_ticker/*.csv` | ~440 | Toàn bộ mã có dữ liệu |

- UI có **2 nút scan riêng**: "Scan đã lọc (~393)" và "Scan tất cả (~440)"
- `filter_cache(data=st.session_state.scan_cache)` — truyền data để tránh đọc disk trong cùng render cycle

### Quick Scan — Amibroker Data trong Cache
`scan_ami_watchlist()` merge dữ liệu từ `get_ami_scan_data()` vào mỗi record:
```python
# screener.py — _AMI_REC_LABELS
{3: "STRONG BUY", 2: "ACCUMULATE", 1: "WATCHING", -2: "RISK SELL", -3: "TOP SELL"}

# Fields được merge vào cache (từ scan_result.csv):
rec["ami_rec"]       # int: 3/2/1/-2/-3
rec["ami_rec_label"] # str: "STRONG BUY" / "ACCUMULATE" / ...
rec["ami_score"]     # float: 0-100 (có thể âm)
rec["ami_setup"]     # str | None: "FLAT BASE" / "VCP TIGHT" / ... (None nếu "---")
rec["ami_forecast"]  # str | None: "BULL DIV" / "BEAR DIV" / "BB BOT REV" (None nếu "---")
```

### Quick Scan — Filters (7 bộ lọc)
```
Hàng 1: Tín hiệu Python | Rủi ro | Giai đoạn | AI Score | Ami Rec
Hàng 2: Setup (Ami)     | Forecast (Ami)
```
⚠️ **Consensus apply()**: Dùng **list comprehension** thay `df.apply(_con_label, axis=1)` để tránh pandas ValueError "Cannot set DataFrame with multiple columns":
```python
df_scan = df_scan.reset_index(drop=True).copy()
df_scan["consensus"] = [_con_label(df_scan.iloc[_i]) for _i in range(len(df_scan))]
```

### Quick Scan — Bảng Kết Quả (display_cols)
Mã | Giá | RSI | Dist EMA34% | ATR% | BB Width% | Vol Ratio | Điểm KT | Đồng thuận | Tín hiệu | Rủi ro | Giai đoạn | **Ami Rec** | **Ami Score** | **Setup** | **Forecast**

### Quick Scan — Auto Price-Refresh
```python
# Toggle + countdown trong session_state:
if _auto_refresh_price:
    _elapsed = time.time() - st.session_state.scan_last_auto_refresh
    _remain  = max(0, int(_interval_secs - _elapsed))
    if _elapsed >= _interval_secs:
        st.session_state.scan_cache = refresh_prices(source=source)
        st.session_state.scan_last_auto_refresh = time.time()
        st.rerun()
    else:
        time.sleep(min(30, _remain))
        st.rerun()
# Đặt ở CUỐI render, sau khi UI đã vẽ xong
```

### scan_ami_watchlist vs scan_watchlist
- `scan_ami_watchlist(with_lstm=True)` — đọc Amibroker + LSTM, không cần vnstock, nhanh
- `scan_watchlist()` — gọi vnstock, bị rate limit, chậm → chỉ dùng fallback
- Quick Scan luôn dùng `scan_ami_watchlist`
- **Parallel scan**: `ThreadPoolExecutor(max_workers=8)`, giảm xuống 2 khi `with_lstm=True` (Keras không thread-safe)
- **Progress**: dùng `threading.Lock()` + counter `[0]` để serialize `st.progress()` từ nhiều thread

### Mã bị hạn chế giao dịch — 3 lớp bảo vệ
```python
# config.py
RESTRICTED_SYMBOLS: set[str] = {"BCG", "FLC", "ROS", ...}  # cập nhật thủ công

# screener.py — scan_ami_symbol()
if symbol.upper() in RESTRICTED_SYMBOLS:
    rec["stock_status"] = "restricted"

# filter_cache(exclude_restricted=True) — mặc định lọc bỏ
# BAD_STATUSES = {"restricted", "suspended", "delisted", "warning"}

# app.py — Khuyến Nghị Nhanh PHẢI lọc riêng (dùng _df_full, không qua filter_cache)
_mask = _df_full["symbol"].isin(RESTRICTED_SYMBOLS)
if "stock_status" in _df_full.columns:
    _mask |= _df_full["stock_status"].isin(_BAD_STATUSES)
_df_rec = _df_full[~_mask].copy()
```
⚠️ **Không** fetch `get_stock_status()` trong scan loop — quá chậm. Dùng static blacklist.

### Khuyến Nghị Nhanh
- Đọc từ **full cache** (`load_cache()`) chứ không từ `df_scan` (đã filtered)
- Nếu đọc từ filtered → thay đổi khi user đổi filter → UX xấu
- **Phải lọc RESTRICTED_SYMBOLS riêng** — `_df_full` không qua `filter_cache()`

### vnstock RetryError trong @st.cache_data
```python
# SATANH: RetryError không phải Exception thông thường — không bị cache bắt nếu raise ra ngoài
# PHẢI catch bên trong hàm cached:
@st.cache_data(ttl=60)
def _fetch_price(sym, d, src):
    try:
        df = get_price_history(sym, days=d, source=src)
    except Exception:
        return None  # cache lưu None, không raise
    return add_all_indicators(df) if df is not None else None

# Trong data.py — wrap vnstock call:
try:
    df = stock.quote.history(...)
except (RetryError, ValueError) as e:
    raise ValueError(f"Không lấy được giá cho '{symbol}': {e}") from e
```

## Tab Phái Sinh (phaisinh_tab.py)

### File data — TÁCH BIỆT (cập nhật 21/06/2026)
| File | Dùng cho | Ghi bởi |
|------|----------|---------|
| `C:\AmibrokerData\data_feed.csv` | **Chứng khoán cơ sở** (daily) | AFL cũ — KHÔNG đổi |
| `C:\AmibrokerData\vn30f1m_1min.csv` | **Bot phái sinh** (1 phút VN30F1M) | AFL Section 16 |
| `C:\AmibrokerData\lstm_brain.keras` | Model LSTM phái sinh | UI train trong tab Phái Sinh |
| `C:\AmibrokerData\lstm_scaler.pkl` | Scaler cho model trên | Cùng với model |

⚠️ **KHÔNG bao giờ** ghi đè `data_feed.csv` bằng dữ liệu 1 phút — hai file phải tách biệt.

### AFL Section 16 — Export VN30F1M 1 phút
Thêm vào cuối Wyckoff VSA AFL (sau Section 15):
```afl
_SECTION_BEGIN("Data Feed Export");
DF_Path = "C:\\AmibrokerData\\vn30f1m_1min.csv";
if( Status("action") == actionExplore AND Name() == "VN30F1M" )
{
    // Ghi header khi stocknum==0, append từng dòng
    // Format: DD/MM/YYYY,HH:MM,O,H,L,C,V (500 nến gần nhất)
}
_SECTION_END();
```
- Chạy trên chart **VN30F1M, timeframe 1 phút** trong Amibroker Explorer
- Dữ liệu lịch sử: 56,382 nến (11/07/2025 → 19/06/2026), lưu tại `E:\AmiBroker\ITD\V\VN30F1M`

### LSTM Train tích hợp trong UI
- Expander "🧠 Train / Retrain Model LSTM" ở cuối tab Phái Sinh
- Hàm `_run_lstm_training()` trong `phaisinh_tab.py`
- Hiển thị progress bar từng epoch qua `_StreamlitCallback`
- Sau train: `st.cache_resource.clear()` để bot load model mới ngay
- **Tham số mặc định**: SEQ_LEN=30, future_bars=5, profit_target=1.0đ, epochs=30
- **Features**: RSI, MACD, Dist_EMA, Log_Ret, Vol_Change (5 features — khớp với inference)

### Tổng quan
Bot tín hiệu VN30F1M: đọc data từ Amibroker → tính LSTM + Multi-TF trend → quản lý lệnh Trailing Stop.

### Kiến trúc
```python
@st.cache_resource
def _load_ai_system():  # (scaler, model) hoặc (None, error_str)
    # Tìm theo thứ tự: D:\AmibrokerData, C:\AmibrokerData, ./AmibrokerData
    # Model: lstm_brain.keras + lstm_scaler.pkl (khác với stock_lstm_v7 của Tab KT)

def _calculate_features(df):
    df.dropna(subset=["RSI", "MACD", "EMA_34"], inplace=True)  # bỏ warmup NaN, KHÔNG fillna(0)
    df[["Log_Ret", "Vol_Change"]] = df[["Log_Ret", "Vol_Change"]].fillna(0)  # chỉ 2 field này OK

def _get_ai_prediction(df_1m, scaler, model) -> tuple[float, str | None]:
    # Trả (prob, warning_msg) — warning nếu feature ngoài phạm vi training ±20%

def _get_trend_from_1m(df_1m) -> tuple[int, str]:
    # Multi-TF: Daily EMA20, 1H EMA20, 15m EMA10 (resample từ 1m data)
    # +1 UPTREND | -1 DOWNTREND | 0 CONFLICT

def _in_trading_session(now=None) -> tuple[bool, str]:
    # VN30F1M: 9:00-11:30 và 13:00-14:45 (Vietnam giờ)
    # Cảnh báo 5 phút trước đóng cửa (_WARN_BEFORE_CLOSE_MIN = 5)
    # Block tín hiệu cho_vao nếu ngoài phiên

def render_phaisinh_tab():  # entry point từ app.py
```

### Session State Keys (prefix `ps_`)
```python
# Tất cả key dùng prefix ps_ để tránh conflict với tabs khác
st.session_state["ps_active_trade"]   # dict hoặc None (lệnh đang chạy)
st.session_state["ps_log_history"]    # list dict — lịch sử lệnh
st.session_state["ps_last_time"]      # timestamp candle cuối xử lý
st.session_state["ps_last_mtime"]     # mtime file csv lần check trước
st.session_state["ps_df_1m"]          # DataFrame cache
st.session_state["ps_last_prob"]      # float — giữ prob qua rerun
st.session_state["ps_last_trend"]     # (int, str) — trend cache
st.session_state["ps_trend_mtime"]    # mtime khi trend cuối được tính
st.session_state["ps_errors"]         # list str — log lỗi trading
st.session_state["ps_ai_warn"]        # str | None — cảnh báo scaler out-of-range
```

### Trend Caching (by mtime)
```python
# CHỈ tính lại trend khi Amibroker ghi file mới (không phải mỗi rerun)
cur_mtime = os.path.getmtime(data_file)
if cur_mtime != st.session_state["ps_trend_mtime"] and df_1m is not None:
    trend, trend_text = _get_trend_from_1m(df_1m)
    st.session_state["ps_last_trend"] = (trend, trend_text)
    st.session_state["ps_trend_mtime"] = cur_mtime
```

### Strict Trend vs Filter Trend
```python
if strict_trend:  # option trong sidebar
    cho_vao = (ai_signal == "LONG" and trend == 1) or (ai_signal == "SHORT" and trend == -1)
else:
    # CONFLICT → đổi signal thành WAIT
    if (trend == 1 and ai_signal == "SHORT") or (trend == -1 and ai_signal == "LONG"):
        ai_signal = "WAIT"
    cho_vao = ai_signal != "WAIT"
```

### Error Logging (KHÔNG dùng bare `except: pass`)
```python
except Exception as e:
    err_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {type(e).__name__}: {e}"
    st.session_state["ps_errors"].insert(0, err_msg)
    st.session_state["ps_errors"] = st.session_state["ps_errors"][:50]  # giữ tối đa 50
# UI: hiển thị trong expander "⚠️ Error Log"
```

### Params cấu hình (sidebar)
- `trailing_pts`: khoảng cách trailing stop (points VN30F1M)
- `initial_sl_pts`: stop loss cứng ban đầu
- `thr_buy` / `thr_sell`: ngưỡng xác suất LSTM để vào lệnh LONG/SHORT

### Auto-refresh Pattern (cuối render)
```python
if auto_refresh:
    time.sleep(1)
    st.rerun()
# KHÔNG dùng while-loop — gây block Streamlit
```

### Cảnh báo Telegram
- Cần `.env`: `TELEGRAM_TOKEN` và `TELEGRAM_CHAT_ID`
- Spam filter lưu tại `data/alert_history.json`
- Cooldown mặc định: 3 ngày / symbol+signal
- Dry run: preview kết quả trước khi gửi thật

### Telegram từ AFL (wycoff AFL) — Bug đã biết
AFL gửi Telegram trực tiếp qua `SendTelegramMessage_Safe()`. Khi `Current_Rec <= -2`:
```afl
// Reason_Str được tính TRƯỚC:
if(Exit_Hard_Stop[i]) Reason_Str = "STOP LOSS (ATR)";  // có thể override RISK SELL

// Message SELL chỉ hiện Reason_Str, không hiện tên tín hiệu:
Msg = "BAN: " + Name() + "\nLy do: " + Reason_Str;  // ← thiếu "Tin hieu: RISK SELL"
```
**Fix** (sửa trong Formula Editor):
```afl
Msg = "BAN: " + Name() + "\nPrice: " + NumToStr(C[i], 1.2) +
      "\nTin hieu: " + WriteIf(Current_Rec <= -3, "TOP SELL", "RISK SELL") +
      "\nLy do: " + Reason_Str;
```

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
