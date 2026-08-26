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
│   ├── phaisinh_tab.py      # Tab Phái Sinh: VN30F1M Signal Bot v4 (VWAP + MACD hist)
│   ├── daily_report.py      # Báo cáo lãi/lỗ cuối ngày + gửi email SMTP
│   ├── alerter.py           # Composite score, spam filter, Telegram (+ tg_escape dùng chung)
│   ├── portfolio.py         # CSV upload, PnL, sector allocation
│   └── cli.py               # CLI: scan + list
├── data/
│   ├── scores_cache.json    # Cache scan (tự tạo)
│   ├── alert_history.json   # Lịch sử cảnh báo Telegram (spam filter)
│   ├── model_metrics.json   # Metrics lần train gần nhất
│   └── train_running.log    # Log training đang chạy
├── tasks/
│   ├── todo.md              # Nhật ký thay đổi lớn + việc còn tồn
│   └── lessons.md           # Bài học từ lỗi đã gặp — ĐỌC ĐẦU MỖI SESSION
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
| Phái Sinh | VN30F1M Signal Bot v4: VWAP phiên + MACD histogram, quản lý vị thế (30 phút / SL 3×ATR), báo cáo lãi lỗ + email |

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
- Expander "🧠 Train / Retrain Model LSTM v2 (3-class)" ở cuối tab Phái Sinh
- Hàm `_run_lstm_training()` trong `phaisinh_tab.py`
- Hiển thị progress bar từng epoch qua `_StreamlitCallback`
- Sau train: `st.cache_resource.clear()` để bot load model mới ngay
- **Tham số mặc định UI**: SEQ_LEN=30, future_bars=10, profit_target=0.5đ, epochs=30
- **Features (8)**: `_FEATURES` = RSI, MACD, Dist_EMA, Log_Ret, Vol_Change,
  VWAP_Dist, Session_Gap, ATR_Norm — KHÔNG đổi thứ tự (scaler phụ thuộc)
- **Label 3 class**: 0=SHORT / 1=WAIT / 2=LONG, `class_weight="balanced"`,
  loss `sparse_categorical_crossentropy`, output `Dense(3, softmax)`

⚠️ LSTM **không** tham gia tín hiệu ở chế độ mặc định. Engine v4 là rule-based
(xem dưới); LSTM chỉ dùng khi chọn chế độ `LSTM` hoặc `Ensemble`.

### Engine v4 (cập nhật 14/08/2026) — ĐÃ THAY THẾ HOÀN TOÀN v2/v3

> Bảng điểm 7 thành phần của v3 đã bị **loại bỏ**. Backtest 65.786 nến
> (11/07/2025–13/08/2026, 273 phiên) cho thấy v3 bắn 105 tín hiệu/ngày với edge ~0
> và **lỗ −0,44đ/lệnh sau phí**. Phân rã alpha từng thành phần: thành phần trọng
> số CAO nhất (RSI<40/>60, +2.0) lại **dự báo ngược** (alpha −0,29đ), còn thành
> phần tốt nhất (giá vs VWAP) chỉ được trọng số 0.5. VN30F1M khung 1 phút là
> **momentum**, không phải mean-reversion. Chi tiết: `tasks/todo.md`.

```
LONG : Close > VWAP phiên  AND  MACD histogram tăng so với nến trước
SHORT: Close < VWAP phiên  AND  MACD histogram giảm so với nến trước
```

⚠️ **Tuỳ chọn TP 3R** (`ps_use_tp`, **user đã bật 19/08/2026**). A/B cùng harness: bật cho
+9,8% tổng, nhưng **+69,1/+71,3đ lợi ích dồn vào riêng quý 2025Q3** — bốn quý còn
lại cộng lại chỉ +2,2đ. Thêm nữa kết quả không đơn điệu theo mức TP (2R +736 ·
3R +796 · 4R +703 · tắt +725) ⇒ khớp nhiễu, không khuyến nghị bật.

⚠️ **KHÔNG có take-profit mặc định — và đó là chủ ý.** Đo trên chính cấu hình này:
`không TP +0,356đ/lệnh · 3R +0,347 · 2R +0,276 · 1,5R +0,218 · 1R +0,112`.
Lệnh thắng chạy xa (trung vị +4,25đ, top 10% +12,91đ), cắt đuôi lãi đó thì không
đủ bù 55% lệnh thua. UI hiện `_WIN_PCTL` làm **vùng lãi tham chiếu** (mức giá
lệnh thắng thường đạt) chứ không phải lệnh chốt.

**Thoát lệnh là một phần chiến thuật, không phải phụ trợ:**
- Giữ tối đa `_HOLD_BARS = 30` nến 1 phút
- Hoặc chạm SL `_SL_ATR_MULT = 3.0` × ATR14 (sàn `_MIN_SL_PTS = 1.0`đ)
- Đóng bắt buộc cuối phiên — **không bao giờ giữ qua đêm**
- SL chặt kiểu v3 (pivot 10 nến) bị nhiễu quét trước khi edge kịp hiện → âm

**Chỉ 1 vị thế tại 1 thời điểm** — ràng buộc load-bearing, KHÔNG phải chống spam
UI. Nới ra là quay lại 105 lệnh/ngày và mất toàn bộ edge (lọc bỏ 93,4% tín hiệu thừa).

⚠️ **Gặp tín hiệu NGƯỢC khi đang giữ lệnh thì KHÔNG làm gì cả** — không thoát,
không đảo. 44,2% số lệnh gặp tình huống này (nhiễu khung 1 phút, không phải đảo
chiều thật). Đo được: giữ đến hết `+0,356đ/lệnh` (5/5 quý) · thoát sớm `+0,086`
(−76%, 3/5 quý) · đảo lệnh `+0,047` (−87%). Nhóm thoát sớm lỗ TB −2,32đ.
UI phải nói rõ "cố ý bỏ qua" chứ đừng hiện như một tín hiệu nên hành động.

**Kết quả** (replay qua chính các hàm trong file, phí 0,25đ/lệnh):
```
2.343 lệnh (8,6/ngày) | win 44,9% | +0,349đ/lệnh | +818,2đ = +81.825.000 VND/HĐ
DƯƠNG Ở CẢ 5/5 QUÝ | LONG +0,265đ (n=1.155) | SHORT +0,431đ (n=1.188)
Hoà vốn ở mức phí 0,62đ/lệnh — biên an toàn ~2,5 lần
```

⚠️ **Tín hiệu tính trên nến ĐÃ ĐÓNG** (`_closed_bars()`). v3 dùng nến đang hình
thành: RSI lệch trung bình 3,46 điểm, 15,6% số lần điều kiện đảo trạng thái.

⚠️ **KHÔNG dùng trend đa khung để ra tín hiệu** — đo được alpha của trend=±1
xấp xỉ 0 (IS +0,006 / OOS −0,061), nó chỉ phản ánh drift thị trường. `_get_trend_full()`
vẫn còn nhưng **chỉ để hiển thị tham khảo**.

### Kiến trúc
```python
def _closed_bars(df_1m, n=300) -> pd.DataFrame | None
    # Bỏ nến cuối (đang hình thành). tail(300) > 241 nến/phiên nên VWAP phiên đủ.

def _get_rule_signal(df_1m) -> tuple[str, str, dict]
    # (signal, reason, detail). detail = {close, vwap, mh, mh_prev, above, rising, bar}
    # → UI hiển thị "đang thiếu điều kiện nào" thay vì chỉ báo WAIT trống

def _get_atr(df_1m) -> float | None            # ATR14 trên nến đã đóng, để đặt SL

def _open_position(side, entry, entry_ts, atr, tid=0) -> dict
    # dict có: tid, side, entry, entry_ts, last_ts, sl, risk, atr, bars

def _check_position_exit(pos, new_bars, in_session) -> (bool, str, float, ts)
    # Duyệt MỌI nến từ lúc vào lệnh, KHÔNG chỉ nến cuối — AFL export ~5 phút/lần
    # nên mỗi lần thấy "nến mới" có thể đã trôi qua nhiều nến.
    # Ưu tiên: sang ngày mới → đóng cuối phiên | SL | hết hạn giữ | ngoài phiên

def _position_pnl(pos, exit_price) -> float    # PnL gộp, chưa trừ phí
def _calc_session_stats(log) -> dict           # total/wins/losses/win_rate/total_pnl/net_pnl
def _get_trend_full(df_1m) -> (int, str, dict) # CHỈ hiển thị, không ra tín hiệu
def _get_stop_levels(df_1m) -> dict            # Buy/Sell Stop — chỉ tham khảo
def render_phaisinh_tab()                      # entry point từ app.py
```

⚠️ `pos["last_ts"]` là con trỏ nến đã xử lý — lọc `new_bars` theo `last_ts`,
**KHÔNG** theo `entry_ts`, nếu không `bars` bị đếm trùng mỗi lần gọi lại.

### Session State Keys (prefix `ps_`)
```python
st.session_state["ps_position"]       # dict | None — vị thế ảo (1 tại 1 thời điểm)
st.session_state["ps_trade_seq"]      # int — bộ đếm mã lệnh trong phiên
st.session_state["ps_last_closed"]    # dict | None — lệnh vừa đóng (banner nhận biết)
st.session_state["ps_log_history"]    # list dict — cặp MỞ/ĐÓNG
st.session_state["ps_rule_signal"]    # "LONG" | "SHORT" | "WAIT"
st.session_state["ps_rule_reason"]    # str — lý do dạng chữ
st.session_state["ps_rule_detail"]    # dict — giá trị thô 2 điều kiện
st.session_state["ps_last_time"]      # timestamp candle cuối đã xử lý
st.session_state["ps_last_mtime"]     # mtime file csv lần check trước
st.session_state["ps_df_1m"]          # DataFrame cache
st.session_state["ps_last_prob_long"] / ["ps_last_prob_short"]   # LSTM
st.session_state["ps_last_trend"] / ["ps_tf_detail"] / ["ps_trend_mtime"]
st.session_state["ps_signal_audit"]   # list dict — 50 nến gần nhất (chẩn đoán)
st.session_state["ps_errors"]         # list str — log lỗi trading
st.session_state["ps_report_sent_date"]  # str — chống gửi email báo cáo 2 lần/ngày
```
⚠️ `ps_active_trade` (v2) và `ps_last_prob` (v2) **không còn tồn tại**.

### Auto-refresh — dùng `st.fragment`, KHÔNG dùng `sleep + rerun`
```python
@st.fragment(run_every=1)
def _live_panel_auto():   _live_panel_body()

@st.fragment
def _live_panel_manual(): _live_panel_body()
# run_every phải cố định lúc khai báo decorator → cần 2 hàm riêng.
# Fragment chỉ rerun vùng bên trong, app.py và tab khác không bị block.
```

### Bền hoá trạng thái — session_state KHÔNG sống qua F5
```python
data/ps_state.json    # vị thế đang mở + ps_trade_seq  (nghiệp vụ)
data/ps_ui_pref.json  # auto refresh, signal_mode, ngưỡng…  (sở thích UI)
```
⚠️ Mất `ps_position` khi reload không chỉ là mất hiển thị: journal có dòng `MỞ`
mà không bao giờ có `ĐÓNG` → lệnh biến mất khỏi báo cáo lãi/lỗ. Và `ps_trade_seq`
về 0 → mã lệnh trùng → `load_trades()` gộp nhầm giá vào của lệnh này với giá ra
của lệnh kia. Bộ đếm luôn lấy `max(seq trên đĩa, max trade_id trong journal hôm nay)`.

⚠️ Widget có `key` đã được nạp sẵn trong session_state thì **KHÔNG truyền
`value=` / `index=`** nữa — Streamlit cảnh báo và bỏ qua một trong hai. Ghi đĩa
bằng `on_change=`, đừng ghi mỗi lần render (auto-refresh 1s sẽ ghi 1 lần/giây).

### Ngưỡng LSTM đọc từ session_state — nhớ chia 100
```python
# slider lưu SỐ NGUYÊN 50–90, so trực tiếp `prob >= 55` sẽ KHÔNG BAO GIỜ đúng
thr_long  = st.session_state.get("ps_thr_buy",  _DEFAULT_THRESHOLD_BUY  * 100) / 100
thr_short = st.session_state.get("ps_thr_sell", _DEFAULT_THRESHOLD_SELL * 100) / 100
```

### Journal CSV — schema có kiểm tra + tự lưu trữ
```python
_JOURNAL_COLUMNS = ["date","time","ticker","action","price","sl","tp",
                    "tp_method","pnl","trade_id","result","reason"]
_rotate_journal_if_stale(path)  # header lệch → ĐỔI TÊN (không xoá) sang *_legacy_<ts>.csv
```
⚠️ `to_csv(mode="a", header=False)` ghi theo thứ tự cột của DataFrame mà **không**
đối chiếu header sẵn có. File cũ từng trộn 3 thế hệ (7/10/12 trường) khiến
`read_csv(on_bad_lines="skip")` **âm thầm vứt 360/483 dòng**. Đổi `_JOURNAL_COLUMNS`
BẮT BUỘC phải đi kèm cơ chế xoay vòng này.

⚠️ Lọc bản ghi v4 theo **`trade_id`**, KHÔNG theo `action`: bot cũ cũng ghi
`"ĐÓNG SHORT"` nên lọc theo action để lọt 42 dòng pnl rác (−1.976,9 → +64,4).

### Cảnh báo Telegram — PHẢI escape nội dung động
```python
from .alerter import tg_escape as _tg_escape   # dùng chung, alerter không cần streamlit
_send_telegram_async(f"🚀 <b>MỞ {_tg_escape(side)}</b>\n⚡ {_tg_escape(reason)}")
```
⚠️ Gửi với `parse_mode="HTML"`. Một ký tự `<` trong dữ liệu → Telegram từ chối
CẢ tin nhắn: `400 can't parse entities: Unsupported start tag "40(+2)"`.
Lý do tín hiệu v3 chứa `RSI=36.3<40(+2)` ⇒ **không cảnh báo nào tới nơi suốt
nhiều tháng**, mà `_send_telegram_async` lại vứt bỏ giá trị trả về nên hoàn toàn
im lặng. Xem `tasks/lessons.md` mục 6.

- `_send_telegram()` log `response.json()["description"]` khi thất bại
- `_TG_LOG` + `_TG_LOG_LOCK` ở module-level (thread không được chạm session_state)
- UI: expander "📨 Telegram" — trạng thái, nút Gửi thử, nhật ký; tự mở khi có lỗi
- `alerter.py` (cảnh báo cổ phiếu) dùng cùng `tg_escape()` + log description

### Định dạng số — `fmt_vn()` trong `alerter.py`
```python
from .alerter import tg_escape as _tg_escape, fmt_vn as _fvn
_fvn(1234567.89, 2)            # "1.234.567,89"  (chấm=nghìn, phẩy=thập phân)
_fvn(-105000, 0, signed=True)  # "-105.000"
```
⚠️ Đặt ở `alerter.py` vì module đó KHÔNG phụ thuộc streamlit; `daily_report`
import ngược `phaisinh_tab` nên không thể là nơi ở của helper dùng chung.
**Một định nghĩa duy nhất** — trước đây UI hiện `-105,000đ` kiểu Mỹ còn email
hiện `-105.000đ` chuẩn VN, cùng một app hai chuẩn.

⚠️ PnL sau phí phải hiện **2 chữ số thập phân**: PnL gộp bước 0,1 nhưng phí
0,25 nên net luôn lẻ 0,05 — làm tròn 1 chữ số cho ra `-1,1đ` cạnh `-105.000đ`
(tức 1,05đ), nhìn như sai số liệu.

### Báo cáo lệnh phái sinh — `vn_invest/daily_report.py`
```python
load_trades(journal_file, day=, day_from=, day_to=)   # ghép cặp MỞ↔ĐÓNG theo trade_id
summarize_by_period(trades, freq)  # "D"/"W"/"M" — nhãn VN: Tuần 30/2026 (20/07–26/07)
build_range_report(d0, d1, freq)   # → (trades, summary, by_period, html)
summarize(trades)                 # + profit factor, max drawdown, theo chiều/lý do thoát
build_email_html(trades, s, day)  # inline style, nền sáng (mail client không đọc <style>)
send_report_email(html, subject)  # SMTP STARTTLS, trả (ok, msg) — không ném lỗi
email_ready()                     # kiểm tra cấu hình trước, báo rõ thiếu biến nào
fmt_vn(value, decimals, signed)   # chuẩn VN: chấm=nghìn, phẩy=thập phân
```
⚠️ Lọc khoảng ngày phải làm **SAU khi ghép cặp** và theo **ngày ĐÓNG lệnh**. Lọc
trước trên toàn bộ bản ghi sẽ cắt mất dòng `MỞ` của lệnh mở từ hôm trước → lệnh
đó bị coi như chưa đóng và biến mất khỏi báo cáo.

⚠️ CSV xuất bằng `utf-8-sig` (BOM) để Excel đọc đúng tiếng Việt.
⚠️ `daily_report` import `phaisinh_tab` để lấy hằng số → `phaisinh_tab` phải
import ngược **bên trong hàm** `_render_daily_report()`, không đặt ở module-level.

⚠️ Mọi số nhúng vào HTML email PHẢI qua `fmt_vn()` — mail client hiển thị đúng y
chuỗi text, không tự áp locale.

Cấu hình `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`REPORT_EMAIL_TO`, `REPORT_EMAIL_FROM`. Gmail bắt buộc **App Password**.


### Dự phòng nóng: hai cửa sổ là CÓ CHỦ Ý (Phase 25–26)
User cố ý mở 2 cửa sổ trình duyệt trên cùng 1 server Streamlit (một trình duyệt
hay bị đóng khi lấy cookie đăng nhập). Cửa sổ thứ hai là **dự phòng**, không phải
lỗi — đừng thiết kế như thể nó sai.

```python
_reload_from_disk()   # nap lai vi the + bo dem + nhat ky tu dia
                      # BAT BUOC goi khi chuyen tu "chi xem" sang "dieu khien"
```
⚠️ **Tiếp quản mà không nạp lại đĩa = mở lệnh trùng.** `ps_position` trong RAM của
cửa sổ dự phòng là bản chụp lúc nó khởi động, có thể cũ hàng giờ. Không nạp lại
thì nó không thấy lệnh cửa sổ kia đang mở ⇒ mở thêm lệnh thứ hai, lệnh cũ kẹt với
dòng MỞ không có ĐÓNG.

⚠️ Nhịp tim ghi đĩa **tiết chế 10s** — fragment chạy 1 lần/giây, ghi mỗi giây là
thừa. TTL 90s ≫ 10s nên không có nguy cơ hết hạn oan.

Test: `scratchpad/test_takeover.py` — A mở lệnh → A chết → B tiếp quản → B phải
thấy lệnh của A, không cấp trùng mã, journal ghép cặp đúng.

### Chống hai phiên Streamlit chạy song song (Phase 25)
`st.session_state` là RIÊNG từng tab. Hai tab cùng mở tab Phái Sinh ⇒ hai engine
chạy song song, cùng ghi một journal ⇒ mã lệnh trùng, dòng trùng, vị thế chồng
nhau. Đã xảy ra thật: 6/56 dòng trùng, 4 mã lệnh bị dùng cho hai lệnh khác nhau.

```python
_OWNER_TTL_SEC = 90
_session_token()      # uuid theo phiên trình duyệt
_read_owner()         # -> (token chủ sở hữu, số giây kể từ nhịp tim cuối)
_claim_ownership()    # -> (là_chủ_sở_hữu, tuổi nhịp tim phiên kia); gọi đầu _live_panel_body()
_can_trade()          # -> ps_is_owner; CHẶN TẬN GỐC _append_journal + _save_ps_state
                      #    + cả khối `if df_1m is not None ... and _can_trade():`
_next_trade_id()      # mã lệnh DUY NHẤT TOÀN CỤC, đọc max trade_id từ ĐĨA
_is_duplicate_of_last(row)   # bỏ qua dòng trùng hệt dòng cuối journal
```

⚠️ **Mã lệnh KHÔNG được reset theo ngày.** `load_trades()` gộp bằng
`groupby("trade_id")`, nên báo cáo nhiều ngày sẽ ghép lệnh #1 của ngày A với
lệnh #1 của ngày B.

⚠️ **Đừng chỉ cảnh báo — phải chặn tận gốc.** Đặt guard ở `_append_journal()` và
`_save_ps_state()` chứ không chỉ ở chỗ gọi, vì các chỗ gọi nằm rải rác 5 nơi.

### Ghép cặp MỞ↔ĐÓNG chịu được dữ liệu hỏng — `_pair_by_time()`
```python
# SAI: tin trade_id là duy nhất
c = cl.iloc[-1]; o = op.iloc[0]     # ghép giá vào lệnh này với giá ra lệnh kia

# ĐÚNG: duyệt theo thứ tự thời gian, ghép MỞ với ĐÓNG kế tiếp
for o, c in _pair_by_time(g): ...
```
Sửa hàm ĐỌC thay vì sửa file dữ liệu — không mất lịch sử. Kèm
`drop_duplicates(subset=["time","action","price","trade_id"])`.

### `hold_min` đếm theo PHÚT GIAO DỊCH — `_trading_minutes()`
Trừ nghỉ trưa 11:30–13:00. Lệnh vào 11:29 ra 13:29 là **30 nến**, không phải
120 phút. Đếm bằng đồng hồ cho ra những dòng trông như bot chạy sai luật.

### Đã đối chiếu dữ liệu thật (24/08/2026) — KHÔNG sửa thuật toán
26 lệnh/5 phiên: win 34,6%, −0,365đ/lệnh, **0/26 vi phạm luật 30 nến**.
Bootstrap 200.000 lần từ 1.862 lệnh lịch sử: khoảng 90% thường gặp khi n=26 là
**[−1,767đ ; +2,859đ]**, xác suất tệ bằng/hơn **28,4%**, xác suất vẫn lỗ sau 26
lệnh dù edge đúng **38,1%**. ⇒ Không có bằng chứng bot hỏng.

⚠️ **Không lọc theo giờ vào lệnh.** Dữ liệu thật gợi ý bỏ 09h (6 lệnh thắng 0%),
nhưng 273 phiên cho thấy 09h là khung TỐT (+0,483đ, IS +0,471 / OOS +0,495).
Luật có nguyên lý "không mở lệnh khi còn <K phút trước giờ đóng phiên" quét
K=0..40: không đơn điệu, OOS/IS tụt 0,68→0,48 ⇒ khớp nhiễu. Xem `lessons.md` 18–19.

### Error Logging (KHÔNG dùng bare `except: pass`)
```python
except Exception as e:
    err_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {type(e).__name__}: {e}"
    st.session_state["ps_errors"].insert(0, err_msg)
    st.session_state["ps_errors"] = st.session_state["ps_errors"][:10]
# UI: expander "🐛 Lỗi hệ thống", tự mở khi có lỗi
```

### Chặn giao dịch khi dữ liệu cũ
```python
_MAX_STALE_MIN = 5
_data_age_min(df_1m) -> float | None   # phút kể từ nến cuối trong DỮ LIỆU
data_stale = data_age > _MAX_STALE_MIN  -> ai_signal = "WAIT"
```
⚠️ **KHÔNG tin mtime của file để đánh giá độ mới.** AFL vẫn ghi đè
`vn30f1m_1min.csv` đều đặn ngay cả khi Amibroker mất kết nối nguồn intraday —
file "mới 2 phút" mà nến cuối bên trong là của **hôm trước**. Đo tuổi bằng
`df_1m.index[-1]`, không phải `os.path.getmtime()`.

Cảnh báo phải phân biệt 2 nguyên nhân (chữa khác nhau):
| Triệu chứng | Nguyên nhân | Cách chữa |
|---|---|---|
| mtime cũ + nến cũ | AFL chưa chạy Explorer | chạy lại Explorer |
| **mtime mới + nến cũ** | nguồn dữ liệu Amibroker đã dừng | kiểm tra kết nối/đăng nhập data feed |

⚠️ Vị thế mở từ phiên trước sẽ **kẹt vô hạn** nếu nguồn dừng: không có nến mới
nào để `_check_position_exit()` kích hoạt. Có guard chạy mỗi lần render — nếu
`pos["entry_ts"].date() != hôm nay` thì chốt tại giá đóng cuối cùng biết được
với lý do "Đóng cuối phiên (dữ liệu dừng)".

### Giờ giao dịch
```python
_in_trading_session(now=None) -> (bool, str)
# Phiên 1: 09:00–11:30 | Phiên 2: 13:00–14:45
# Cảnh báo 5 phút trước đóng cửa (_WARN_BEFORE_CLOSE_MIN = 5)
# Ngoài phiên → ai_signal = "WAIT" (chặn vào lệnh mới, KHÔNG chặn thoát lệnh)
```

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

## Cảnh báo cổ phiếu cơ sở — chạy TÁCH KHỎI app

`alert_watcher.py` là tiến trình nền riêng, **không** chạy kèm Streamlit. Mở app
không bật nó. Đây là thứ gửi Telegram tự động khi AmiBroker quét xong.

- Tự chạy cùng Windows: Task Scheduler **`VNInvest_AlertWatcher`** (AtLogOn,
  `pythonw.exe`, tự restart 999 lần/5 phút)
- Chạy tay dự phòng: `start_alert_watcher.bat`
- Tab Model AI có panel báo watcher sống/chết (đọc mtime `data/alert_watcher.log`)

⚠️ Chạy bằng `pythonw.exe` thì **`sys.stdout is None`** — mọi `sys.stdout.xxx`
phải guard, nếu không script chết với mã lỗi 1 mà KHÔNG để lại dòng log nào.

⚠️ Có mutex `VNInvest_AlertWatcher_Mutex` chống chạy 2 tiến trình cùng lúc
(task tự động + user bấm .bat) gây gửi trùng.

⚠️ Xếp hạng chọn top `max_alerts`: KHÔNG sort composite thô. Với SELL thì điểm
THẤP mới mạnh, sort giảm dần sẽ chọn lệnh bán yếu nhất. Dùng `_strength()`
(khoảng cách vượt ngưỡng) + **chia suất cho cả hai chiều**, nếu không phiên lệch
một bên là chiều đó chiếm sạch. Xem `tasks/lessons.md` mục 9.

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

# Email báo cáo lãi/lỗ cuối ngày (tab Phái Sinh) — Gmail cần App Password
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
REPORT_EMAIL_TO=...
REPORT_EMAIL_FROM=
```

Xem `.env.example` để biết đầy đủ và ghi chú từng biến.

## Sửa file bằng script — BẮT BUỘC ghi nguyên tử

```python
# ❌ SAI — "w" truncate file NGAY, lỗi encode sau đó là mất trắng
io.open(P, "w", encoding="utf-8").write(src)

# ✅ ĐÚNG
compile(src, P, "exec")        # cú pháp sai -> dừng, file đích còn nguyên
data = src.encode("utf-8")     # encode sai  -> dừng, file đích còn nguyên
tmp = P + ".tmp"
with open(tmp, "wb") as f:
    f.write(data)
os.replace(tmp, P)             # thay thế nguyên tử
```
⚠️ Ngày 25/08/2026 mẫu sai ở trên đã xoá sạch `phaisinh_tab.py` (136.641 byte).
Khôi phục được nhờ `.pyc` + transcript — xem `tasks/lessons.md` mục 20–21.

⚠️ **Không viết emoji bằng `\uXXXX`** cho ký tự ngoài BMP: `\ud83d\udee1` là cặp
surrogate, hợp lệ trong chuỗi Python nhưng KHÔNG encode được UTF-8. Viết thẳng
ký tự hoặc dùng `\U0001F6E1`.

### Chống spam Telegram — HAI lớp, đừng bỏ lớp nào

```python
_TG_DEDUP_SEC = 90
_tg_is_duplicate(msg)   # bam sha256 noi dung, bo qua tin y het trong 90s
```
⚠️ **Chốt chặn "mỗi nến một lần" phải commit NGAY sau khi kiểm tra**, trước mọi
lần gửi Telegram / ghi journal:
```python
if last_time != st.session_state["ps_last_time"]:
    st.session_state["ps_last_time"] = last_time   # <- NGAY DAY
    ... gui Telegram, ghi journal ...
```
Đặt commit ở cuối khối `try` (như bản cũ) thì một exception giữa chừng khiến nến
cũ vẫn "mới" ở lần render sau — fragment chạy 1 lần/giây ⇒ **~60 tin/phút**.
Bỏ lỡ 1 nến còn hơn spam 60 tin.

⚠️ Dấu vết phân biệt nguyên nhân spam: đếm số lần lặp mỗi bản ghi journal.
Lặp **đúng ×2** = hai cửa sổ cùng chạy. Lặp **10–60 lần** = chốt chặn commit muộn.

### Nhãn ⭐ TÍN HIỆU MẠNH — gắn nhãn, KHÔNG lọc (Phase 27)
```python
_STRONG_DMH_ATR = 0.10   # |Δ MACD hist| >= 0.10 × ATR14 tại nến tín hiệu
# detail["dmh_atr"], detail["strong"]; reason có prefix "⭐ MẠNH · " khi strong
```
Đo 273 phiên trên cùng dòng lệnh chuẩn: nhóm mạnh (~14% số lệnh, ~1/ngày)
**+1,454đ/lệnh** (IS +1,660 / OOS +1,301 · 5/5 quý · cả 2 chiều dương ·
p hoán vị = 0,007) so với +0,285đ nhóm thường. Quét 0.06→0.14 đơn điệu, đỉnh
bằng phẳng 0.09–0.12 — không phải đỉnh nhọn khớp nhiễu.

⚠️ **KHÔNG được nâng cấp nhãn thành bộ lọc.** Nhóm thường vẫn cộng +459đ tổng —
lọc bỏ là vứt tiền. Nhãn chỉ để người theo lệnh thủ công ưu tiên. Nhãn nằm trong
`reason` nên journal ghi lại được — đối chiếu nhóm mạnh/thường trên dữ liệu thật
sau ~2-3 tháng trước khi cân nhắc bất kỳ thay đổi hành vi nào.

Đã thử và BÁC BỎ ở vòng 4 (đừng thử lại): bỏ một chiều LONG/SHORT, lọc khoảng
cách tới VWAP, dấu mh đồng pha, bền vững 2 nến, nghỉ sau SL, cầu dao thua K
lệnh/ngày — tất cả đều giảm tổng lợi nhuận so với v4 chuẩn (+844đ).

