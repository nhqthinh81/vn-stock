"""Tab Phái Sinh — VN30F1M Signal Bot (Multi-TF + Trailing Stop).

Fixes vs v1:
- Trend cached by mtime: không recompute 5000-row resample mỗi giây
- prob lưu session_state: hiển thị đúng khi đang giữ lệnh
- Lỗi trading logic log ra UI thay vì nuốt im lặng
- LSTM features: drop warmup rows thay vì fillna(0)
- Cảnh báo scaler out-of-range khi giá ngoài training range
- Kiểm tra giờ giao dịch VN30F1M, cảnh báo cuối phiên
- Option "Chỉ trade khi đa khung đồng pha"
- Thống kê session: PnL tổng, win rate, số lệnh
- Trailing stop / SL nhập từ UI
"""
import os
import threading
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd
import streamlit as st

# ── Đường dẫn dữ liệu ────────────────────────────────────────────────────────
_POTENTIAL_PATHS = [
    r"D:\AmibrokerData",
    r"C:\AmibrokerData",
    os.path.join(os.getcwd(), "AmibrokerData"),
]
_BASE_DATA_DIR = next((p for p in _POTENTIAL_PATHS if os.path.exists(p)), None)

if _BASE_DATA_DIR:
    _DATA_FILE_1M = os.path.join(_BASE_DATA_DIR, "vn30f1m_1min.csv")
    _MODEL_PATH   = os.path.join(_BASE_DATA_DIR, "lstm_brain.keras")
    _SCALER_PATH  = os.path.join(_BASE_DATA_DIR, "lstm_scaler.pkl")
    _JOURNAL_FILE = os.path.join(_BASE_DATA_DIR, "vn30_ai_journal.csv")
    _CRED_FILE    = os.path.join(_BASE_DATA_DIR, "credentials.json")
else:
    _DATA_FILE_1M = _MODEL_PATH = _SCALER_PATH = _JOURNAL_FILE = _CRED_FILE = None

# ── Hằng số chiến thuật (default — có thể ghi đè qua UI) ────────────────────
_SEQ_LEN             = 30
_DEFAULT_THRESHOLD_BUY   = 0.60
_DEFAULT_THRESHOLD_SELL  = 0.40
_DEFAULT_TRAILING_PTS    = 3.0
_DEFAULT_INITIAL_SL_PTS  = 3.0
_GSHEET_NAME         = "VN30_Trading_Journal"

# ── Giờ giao dịch VN30F1M ────────────────────────────────────────────────────
_SESSION1_START = dtime(9, 0)
_SESSION1_END   = dtime(11, 30)
_SESSION2_START = dtime(13, 0)
_SESSION2_END   = dtime(14, 45)
_WARN_BEFORE_CLOSE_MIN = 5   # cảnh báo trước khi đóng phiên N phút


def _in_trading_session(now: dtime | None = None) -> tuple[bool, str]:
    """Trả (đang_trong_phiên, thông_điệp_trạng_thái)."""
    if now is None:
        now = datetime.now().time()
    if _SESSION1_START <= now <= _SESSION1_END:
        # Kiểm tra sắp hết phiên 1
        from datetime import timedelta
        dt_now  = datetime.combine(datetime.today(), now)
        dt_end1 = datetime.combine(datetime.today(), _SESSION1_END)
        mins_left = (dt_end1 - dt_now).seconds // 60
        if mins_left <= _WARN_BEFORE_CLOSE_MIN:
            return True, f"⚠️ Phiên 1 đóng cửa sau {mins_left} phút (11:30)"
        return True, f"🟢 Phiên 1 ({_SESSION1_START.strftime('%H:%M')}–{_SESSION1_END.strftime('%H:%M')})"
    if _SESSION2_START <= now <= _SESSION2_END:
        from datetime import timedelta
        dt_now  = datetime.combine(datetime.today(), now)
        dt_end2 = datetime.combine(datetime.today(), _SESSION2_END)
        mins_left = (dt_end2 - dt_now).seconds // 60
        if mins_left <= _WARN_BEFORE_CLOSE_MIN:
            return True, f"⚠️ Phiên 2 đóng cửa sau {mins_left} phút (14:45)"
        return True, f"🟢 Phiên 2 ({_SESSION2_START.strftime('%H:%M')}–{_SESSION2_END.strftime('%H:%M')})"
    if now < _SESSION1_START:
        return False, f"⏳ Chờ mở phiên 1 (09:00)"
    if _SESSION1_END < now < _SESSION2_START:
        return False, "⏸ Nghỉ trưa (11:30–13:00)"
    return False, "🔴 Ngoài giờ giao dịch (>14:45)"


# ── Load AI model (cached toàn app) ──────────────────────────────────────────
@st.cache_resource
def _load_ai_system():
    if not (_SCALER_PATH and _MODEL_PATH and
            os.path.exists(_SCALER_PATH) and os.path.exists(_MODEL_PATH)):
        return None, None
    try:
        import joblib
        from tensorflow.keras.models import load_model  # type: ignore
        scaler = joblib.load(_SCALER_PATH)
        model  = load_model(_MODEL_PATH)
        return scaler, model
    except Exception as e:
        return None, str(e)   # trả lỗi để UI hiển thị


# ── Feature helpers ───────────────────────────────────────────────────────────

def _clean_num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return np.nan


def _load_df_1m(path: str):
    if not path or not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            if c in df.columns:
                df[c] = df[c].apply(_clean_num)
        if "Date" in df.columns and "Time" in df.columns:
            df.index = pd.to_datetime(df["Date"] + " " + df["Time"], dayfirst=True)
        return df.dropna(subset=["Close"]).copy()
    except Exception as e:
        return None


def _calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """Tính features, drop warmup rows thay vì fillna(0)."""
    import pandas_ta as ta  # type: ignore
    df = df.copy()
    df["RSI"]        = ta.rsi(df["Close"], 14)
    macd             = ta.macd(df["Close"])
    df["MACD"]       = macd.iloc[:, 0] if macd is not None else np.nan
    df["EMA_34"]     = ta.ema(df["Close"], 34)
    df["Dist_EMA"]   = (df["Close"] - df["EMA_34"]) / df["Close"]
    df["Log_Ret"]    = np.log(df["Close"] / df["Close"].shift(1))
    df["Vol_Change"] = df["Volume"].pct_change()
    # Drop warmup NaN thay vì fill 0 — MACD cần ~26 rows, EMA34 cần 34 rows
    df.dropna(subset=["RSI", "MACD", "EMA_34"], inplace=True)
    # Log_Ret / Vol_Change row đầu vẫn NaN → fill 0 (hàng đầu sau warmup, an toàn)
    df[["Log_Ret", "Vol_Change"]] = df[["Log_Ret", "Vol_Change"]].fillna(0)
    return df


def _get_ai_prediction(df_1m: pd.DataFrame, scaler, model) -> tuple[float, str | None]:
    """Trả (prob, warning_msg). warning_msg != None nếu scaler out-of-range."""
    if scaler is None or model is None or len(df_1m) < _SEQ_LEN + 60:
        return 0.5, None
    try:
        df_f  = _calculate_features(df_1m.tail(200))
        if len(df_f) < _SEQ_LEN:
            return 0.5, "Không đủ hàng sau drop warmup"
        cols  = ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change"]
        seq   = df_f[cols].tail(_SEQ_LEN).values

        # Kiểm tra out-of-range so với data scaler đã fit
        warn = None
        if hasattr(scaler, "data_min_") and hasattr(scaler, "data_max_"):
            lo, hi = scaler.data_min_, scaler.data_max_
            if np.any(seq < lo * 0.8) or np.any(seq > hi * 1.2):
                warn = "⚠️ Giá trị feature vượt ngoài range lúc train — dự báo có thể sai"

        scaled = scaler.transform(seq).reshape(1, _SEQ_LEN, len(cols))
        prob   = float(model.predict(scaled, verbose=0)[0][0])
        return prob, warn
    except Exception as e:
        return 0.5, f"Lỗi AI: {e}"


def _get_trend_from_1m(df_1m: pd.DataFrame) -> tuple[int, str]:
    """Multi-TF trend (Daily / 1H / 15m). Nặng — chỉ gọi khi mtime thay đổi."""
    import pandas_ta as ta  # type: ignore
    if df_1m is None or len(df_1m) < 80:
        return 0, "Không đủ dữ liệu"
    agg    = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    recent = df_1m.tail(5000).copy()
    df_15  = recent.resample("15T").agg(agg).dropna()
    df_1h  = recent.resample("60T").agg(agg).dropna()
    df_d   = recent.resample("1D").agg(agg).dropna()
    if len(df_15) < 10 or len(df_1h) < 10 or len(df_d) < 10:
        return 0, "Đang gom dữ liệu đa khung..."
    df_d["EMA20"]  = ta.ema(df_d["Close"], 20)
    df_1h["EMA20"] = ta.ema(df_1h["Close"], 20)
    df_15["EMA10"] = ta.ema(df_15["Close"], 10)
    df_d.dropna(subset=["EMA20"], inplace=True)
    df_1h.dropna(subset=["EMA20"], inplace=True)
    df_15.dropna(subset=["EMA10"], inplace=True)
    if not (len(df_d) and len(df_1h) and len(df_15)):
        return 0, "Đang gom dữ liệu đa khung..."
    t_d  = 1 if df_d.iloc[-1]["Close"]  > df_d.iloc[-1]["EMA20"]  else -1
    t_1h = 1 if df_1h.iloc[-1]["Close"] > df_1h.iloc[-1]["EMA20"] else -1
    t_15 = 1 if df_15.iloc[-1]["Close"] > df_15.iloc[-1]["EMA10"] else -1
    if t_d == t_1h == t_15 == 1:  return  1, "UPTREND (D/H/15p đồng pha)"
    if t_d == t_1h == t_15 == -1: return -1, "DOWNTREND (D/H/15p đồng pha)"
    if t_d == t_1h == 1:          return  1, "UPTREND (D/H đồng pha, 15p nhiễu)"
    if t_d == t_1h == -1:         return -1, "DOWNTREND (D/H đồng pha, 15p nhiễu)"
    return 0, "Xu hướng đa khung xung đột"


# ── Telegram / journal ────────────────────────────────────────────────────────

def _send_telegram(msg: str):
    import requests  # type: ignore
    token   = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=3,
        )
    except Exception:
        pass


def _send_telegram_async(msg: str):
    threading.Thread(target=_send_telegram, args=(msg,), daemon=True).start()


def _append_journal(entry: dict):
    if not _JOURNAL_FILE:
        return
    row = pd.DataFrame([{
        "date":   datetime.now().strftime("%Y-%m-%d"),
        "time":   entry["time"],
        "ticker": entry["ticker"],
        "action": entry["act"],
        "price":  entry["price"],
        "pnl":    entry.get("pnl", ""),
        "reason": entry["reason"],
    }])
    try:
        exists = os.path.exists(_JOURNAL_FILE)
        row.to_csv(_JOURNAL_FILE, mode="a" if exists else "w", header=not exists, index=False)
    except Exception:
        pass


def _sync_gsheet_async(entry: dict):
    if not (_CRED_FILE and os.path.exists(_CRED_FILE)):
        return
    def _run():
        try:
            import gspread  # type: ignore
            from oauth2client.service_account import ServiceAccountCredentials  # type: ignore
            scope  = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds  = ServiceAccountCredentials.from_json_keyfile_name(_CRED_FILE, scope)
            client = gspread.authorize(creds)
            sheet  = client.open(_GSHEET_NAME).sheet1
            sheet.append_row([
                datetime.now().strftime("%Y-%m-%d"),
                entry["time"], entry["ticker"],
                entry["act"],  entry["price"],
                entry.get("pnl", ""), entry["reason"],
            ])
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


# ── Thống kê session từ log ───────────────────────────────────────────────────

def _calc_session_stats(log: list[dict]) -> dict:
    """Tính PnL tổng, win rate, số lệnh từ ps_log_history."""
    closed = [x for x in log if x["act"].startswith("ĐÓNG")]
    if not closed:
        return {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "win_rate": 0.0}
    total_pnl = 0.0
    wins = losses = 0
    for x in closed:
        try:
            pnl = float(str(x["pnl"]).replace("—", "0"))
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
        except Exception:
            pass
    n = wins + losses
    return {
        "total":     n,
        "wins":      wins,
        "losses":    losses,
        "total_pnl": total_pnl,
        "win_rate":  (wins / n * 100) if n else 0.0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# LSTM Training
# ══════════════════════════════════════════════════════════════════════════════

def _run_lstm_training(data_file, model_file, scaler_file,
                       future_bars=5, profit_target=1.0, epochs=30):
    """Train LSTM trực tiếp trong UI, hiển thị progress và kết quả."""
    SEQ_LEN    = 30
    BATCH_SIZE = 64
    TEST_RATIO = 0.15
    FEATURES   = ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change"]

    log = st.empty()

    def _log(msg):
        log.info(msg)

    try:
        import pandas_ta as ta
        import joblib
        from sklearn.preprocessing import MinMaxScaler
        from sklearn.metrics import classification_report
        import tensorflow as tf
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization
        from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    except ImportError as e:
        st.error(f"❌ Thiếu thư viện: {e}. Chạy: `pip install pandas_ta tensorflow scikit-learn joblib`")
        return

    with st.status("🧠 Đang train model LSTM...", expanded=True) as status:

        # 1. Load data
        st.write("📂 Đọc dữ liệu...")
        df = pd.read_csv(data_file)
        df.index = pd.to_datetime(df["Date"] + " " + df["Time"], dayfirst=True)
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.dropna(subset=["Close"], inplace=True)
        df.sort_index(inplace=True)
        st.write(f"   ✅ {len(df):,} nến | {df.index[0].date()} → {df.index[-1].date()}")

        # 2. Features
        st.write("⚙️ Tính features (RSI, MACD, EMA34)...")
        df["RSI"]        = ta.rsi(df["Close"], 14)
        macd             = ta.macd(df["Close"])
        df["MACD"]       = macd.iloc[:, 0] if macd is not None else np.nan
        df["EMA_34"]     = ta.ema(df["Close"], 34)
        df["Dist_EMA"]   = (df["Close"] - df["EMA_34"]) / df["Close"]
        df["Log_Ret"]    = np.log(df["Close"] / df["Close"].shift(1))
        df["Vol_Change"] = df["Volume"].pct_change()

        # 3. Label
        st.write(f"🏷️ Tạo nhãn (tăng ≥ {profit_target} điểm sau {future_bars} nến)...")
        df["future_close"] = df["Close"].shift(-future_bars)
        df["label"]        = (df["future_close"] - df["Close"] >= profit_target).astype(int)
        df.dropna(subset=["RSI", "MACD", "EMA_34", "future_close"], inplace=True)
        df[["Log_Ret", "Vol_Change"]] = df[["Log_Ret", "Vol_Change"]].fillna(0)
        long_pct = df["label"].mean() * 100
        st.write(f"   ✅ {len(df):,} nến sau clean | LONG: {long_pct:.1f}% | SHORT: {100-long_pct:.1f}%")

        # 4. Sequences
        st.write("🔢 Tạo sequences...")
        X_data = df[FEATURES].values
        y_data = df["label"].values
        X_seqs, y_seqs = [], []
        for i in range(SEQ_LEN, len(X_data)):
            X_seqs.append(X_data[i - SEQ_LEN:i])
            y_seqs.append(y_data[i])
        X_seqs = np.array(X_seqs)
        y_seqs = np.array(y_seqs)

        # 5. Scale
        st.write("📐 Scale + train/test split...")
        split = int(len(X_seqs) * (1 - TEST_RATIO))
        X_train, X_test = X_seqs[:split], X_seqs[split:]
        y_train, y_test = y_seqs[:split], y_seqs[split:]
        scaler = MinMaxScaler()
        X_train = scaler.fit_transform(X_train.reshape(-1, len(FEATURES))).reshape(-1, SEQ_LEN, len(FEATURES))
        X_test  = scaler.transform(X_test.reshape(-1, len(FEATURES))).reshape(-1, SEQ_LEN, len(FEATURES))
        joblib.dump(scaler, scaler_file)
        st.write(f"   ✅ Train: {len(X_train):,} | Test: {len(X_test):,}")

        # 6. Build model
        st.write("🏗️ Build model LSTM...")
        model = Sequential([
            LSTM(64, input_shape=(SEQ_LEN, len(FEATURES)), return_sequences=True),
            Dropout(0.2),
            BatchNormalization(),
            LSTM(32, return_sequences=False),
            Dropout(0.2),
            Dense(16, activation="relu"),
            Dense(1, activation="sigmoid"),
        ])
        model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])

        # 7. Train
        st.write(f"🚀 Train ({epochs} epochs max)...")
        progress_bar = st.progress(0)

        class _StreamlitCallback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                pct = int((epoch + 1) / epochs * 100)
                progress_bar.progress(pct, text=f"Epoch {epoch+1}/{epochs} | loss={logs.get('loss',0):.4f} | val_acc={logs.get('val_accuracy',0):.3f}")

        history = model.fit(
            X_train, y_train,
            epochs=epochs,
            batch_size=BATCH_SIZE,
            validation_data=(X_test, y_test),
            callbacks=[
                EarlyStopping(patience=5, restore_best_weights=True, monitor="val_loss"),
                ReduceLROnPlateau(patience=3, factor=0.5, monitor="val_loss"),
                _StreamlitCallback(),
            ],
            verbose=0,
        )

        # 8. Evaluate
        st.write("📊 Đánh giá...")
        _, acc = model.evaluate(X_test, y_test, verbose=0)
        y_pred = (model.predict(X_test, verbose=0).flatten() >= 0.60).astype(int)
        report = classification_report(y_test, y_pred, target_names=["SHORT/WAIT", "LONG"], output_dict=True)

        model.save(model_file)
        status.update(label=f"✅ Train xong! Accuracy: {acc*100:.1f}%", state="complete")

    # Hiển thị kết quả
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Test Accuracy", f"{acc*100:.1f}%")
    r2.metric("LONG Precision", f"{report['LONG']['precision']*100:.1f}%")
    r3.metric("LONG Recall",    f"{report['LONG']['recall']*100:.1f}%")
    r4.metric("LONG F1",        f"{report['LONG']['f1-score']*100:.1f}%")
    st.success(f"✅ Model lưu: `{model_file}` | Scaler: `{scaler_file}`\nRestart app để load model mới.")
    st.cache_resource.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Main render
# ══════════════════════════════════════════════════════════════════════════════

def render_phaisinh_tab():
    """Render toàn bộ tab Phái Sinh. Gọi từ app.py."""
    import time

    # ── CSS ──────────────────────────────────────────────────────────────────
    st.markdown("""
    <style>
        .ps-box    { background:#1E2129;padding:16px;border-radius:10px;
                     text-align:center;border:1px solid #333;margin-bottom:4px; }
        .ps-active { border:2px solid #00E676 !important;background:rgba(0,230,118,.08) !important; }
        .ps-warn   { border:2px solid #FF5252 !important;background:rgba(255,82,82,.08) !important; }
        .ps-log    { width:100%;border-collapse:collapse;margin-top:8px; }
        .ps-log th { background:#222;color:#FFF;padding:10px;text-align:left;border-bottom:2px solid #555; }
        .ps-log td { border-bottom:1px solid #2a2a2a;padding:9px;text-align:center;font-size:.93rem; }
        .c-long  { color:#00E676;font-weight:bold }
        .c-short { color:#FF5252;font-weight:bold }
        .c-pos   { color:#00B0FF;font-weight:bold }
        .c-neg   { color:#FFD600;font-weight:bold }
    </style>
    """, unsafe_allow_html=True)

    # ── Session state defaults ────────────────────────────────────────────────
    _defaults = {
        "ps_active_trade":  None,
        "ps_log_history":   [],
        "ps_last_time":     "",
        "ps_last_mtime":    0,
        "ps_df_1m":         None,
        "ps_last_prob":     0.5,        # FIX: lưu prob liên tục
        "ps_last_trend":    (0, "—"),   # FIX: cache trend theo mtime
        "ps_trend_mtime":   -1,         # mtime lần tính trend gần nhất
        "ps_errors":        [],         # FIX: log lỗi trading ra UI
        "ps_ai_warn":       None,       # cảnh báo scaler out-of-range
    }
    for k, v in _defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    st.header("⚡ VN30F1M — Signal Bot (Multi-TF + Trailing Stop)")

    if not _BASE_DATA_DIR:
        st.error(
            "❌ Không tìm thấy thư mục AmibrokerData tại D:\\ hoặc C:\\. "
            "Đảm bảo Amibroker đang xuất **data_feed.csv** vào đúng đường dẫn."
        )
        return

    # ── Controls ──────────────────────────────────────────────────────────────
    with st.expander("⚙️ Cấu hình chiến thuật", expanded=False):
        cfg_c1, cfg_c2, cfg_c3, cfg_c4 = st.columns(4)
        trailing_pts   = cfg_c1.number_input("Trailing Stop (điểm)", 0.5, 20.0, _DEFAULT_TRAILING_PTS,   0.5, key="ps_trailing")
        initial_sl_pts = cfg_c2.number_input("SL ban đầu (điểm)",   0.5, 20.0, _DEFAULT_INITIAL_SL_PTS, 0.5, key="ps_sl")
        thr_buy        = cfg_c3.slider("Ngưỡng LONG (%)", 50, 90, int(_DEFAULT_THRESHOLD_BUY * 100), key="ps_thr_buy")  / 100
        thr_sell       = cfg_c4.slider("Ngưỡng SHORT (%)", 10, 50, int(_DEFAULT_THRESHOLD_SELL * 100), key="ps_thr_sell") / 100
        strict_trend   = st.checkbox(
            "Chỉ trade khi đa khung đồng pha (D+H+15p cùng chiều)",
            value=False, key="ps_strict_trend",
            help="Bật: chỉ vào lệnh khi cả 3 khung D/H/15p đồng pha. Tắt: cho phép khi D+H đồng pha dù 15p khác."
        )

    top_c1, top_c2, top_c3 = st.columns([1, 1, 2])
    auto_refresh = top_c1.toggle("🔄 Auto Refresh (1s)", value=False, key="ps_auto_toggle")
    if top_c2.button("🚨 Reset Khẩn Cấp", use_container_width=True):
        st.session_state["ps_active_trade"] = None
        st.session_state["ps_errors"]       = []
        st.success("Đã reset về trạng thái Quan sát!")

    # ── Giờ giao dịch ─────────────────────────────────────────────────────────
    in_session, session_msg = _in_trading_session()
    top_c3.caption(session_msg)
    if not in_session and st.session_state["ps_active_trade"] is not None:
        st.warning(
            "⚠️ Đang ngoài giờ giao dịch nhưng còn lệnh mở trong session. "
            "Kiểm tra lại trạng thái lệnh thực tế trên sàn."
        )

    st.divider()

    # ── Load AI ───────────────────────────────────────────────────────────────
    ai_result = _load_ai_system()
    # _load_ai_system trả (scaler, model) hoặc (None, error_str) nếu lỗi load
    if isinstance(ai_result[1], str) and ai_result[0] is None:
        st.error(f"❌ Lỗi load model LSTM: {ai_result[1]}")
        ai_scaler, ai_model = None, None
    else:
        ai_scaler, ai_model = ai_result

    if ai_model is None and not isinstance(ai_result[1], str):
        st.warning(f"⚠️ Chưa có model LSTM (`{_MODEL_PATH}`). Bot chạy chế độ đa khung.")

    # ── Đọc CSV khi Amibroker ghi file mới ───────────────────────────────────
    current_price = 0.0
    last_time     = st.session_state["ps_last_time"]
    ai_signal     = "WAIT"

    try:
        cur_mtime = os.path.getmtime(_DATA_FILE_1M)
        if cur_mtime != st.session_state["ps_last_mtime"]:
            st.session_state["ps_df_1m"]      = _load_df_1m(_DATA_FILE_1M)
            st.session_state["ps_last_mtime"] = cur_mtime
    except Exception:
        pass

    df_1m = st.session_state["ps_df_1m"]

    # ── FIX: Cache trend — chỉ tính lại khi mtime đổi ───────────────────────
    cur_mtime_val = st.session_state["ps_last_mtime"]
    if cur_mtime_val != st.session_state["ps_trend_mtime"] and df_1m is not None:
        trend, trend_text = _get_trend_from_1m(df_1m)
        st.session_state["ps_last_trend"]  = (trend, trend_text)
        st.session_state["ps_trend_mtime"] = cur_mtime_val
    else:
        trend, trend_text = st.session_state["ps_last_trend"]

    # ── FIX: Lấy prob từ session (không reset về 0.5 mỗi rerun) ─────────────
    prob = st.session_state["ps_last_prob"]

    # ── Logic trading (có error logging) ─────────────────────────────────────
    if df_1m is not None and len(df_1m) > 0:
        _trade_error = None
        try:
            current_price = float(df_1m.iloc[-1]["Close"])
            last_time     = df_1m.index[-1].strftime("%Y-%m-%d %H:%M")

            # A. Quản lý lệnh đang mở
            trade = st.session_state["ps_active_trade"]
            if trade is not None:
                exit_triggered, exit_reason = False, ""
                if last_time != trade["entry_time"]:
                    if trade["type"] == "LONG":
                        if current_price > trade["peak"]:
                            trade["peak"] = current_price
                            trade["sl"]   = max(trade["sl"], trade["peak"] - trailing_pts)
                        if current_price <= trade["sl"]:
                            exit_triggered, exit_reason = True, "Chạm Trailing Stop / SL"
                    elif trade["type"] == "SHORT":
                        if current_price < trade["peak"]:
                            trade["peak"] = current_price
                            trade["sl"]   = min(trade["sl"], trade["peak"] + trailing_pts)
                        if current_price >= trade["sl"]:
                            exit_triggered, exit_reason = True, "Chạm Trailing Stop / SL"

                if exit_triggered:
                    pnl  = (current_price - trade["entry"]) if trade["type"] == "LONG" else (trade["entry"] - current_price)
                    icon = "✅" if pnl > 0 else "❌"
                    _send_telegram_async(
                        f"🏁 <b>ĐÓNG LỆNH {trade['type']}</b>\n"
                        f"💰 <b>Giá chốt:</b> {current_price}\n"
                        f"🎯 <b>PnL:</b> {pnl:+.1f} điểm {icon}\n"
                        f"📌 <b>Lý do:</b> {exit_reason}"
                    )
                    log_entry = {
                        "time": last_time, "ticker": "VN30F1M",
                        "act":  f"ĐÓNG {trade['type']}", "price": current_price,
                        "pnl":  f"{pnl:+.1f}", "reason": exit_reason,
                    }
                    st.session_state["ps_log_history"].insert(0, log_entry)
                    _append_journal(log_entry)
                    _sync_gsheet_async(log_entry)
                    st.session_state["ps_active_trade"] = None

            # B. Phát tín hiệu mới (candle mới chưa xử lý)
            elif last_time != st.session_state["ps_last_time"]:
                if ai_model is not None:
                    prob_new, ai_warn = _get_ai_prediction(df_1m, ai_scaler, ai_model)
                    prob = prob_new
                    st.session_state["ps_last_prob"] = prob
                    st.session_state["ps_ai_warn"]   = ai_warn
                conf = prob * 100 if prob >= 0.5 else (1 - prob) * 100

                if prob >= thr_buy:
                    ai_signal = "LONG"
                elif prob <= thr_sell:
                    ai_signal = "SHORT"

                # FIX: Filter "strict trend" option
                if strict_trend:
                    # Chỉ cho vào khi 3 khung đồng pha hoàn toàn
                    cho_vao = (
                        (ai_signal == "LONG"  and trend == 1)
                        or (ai_signal == "SHORT" and trend == -1)
                    )
                    if ai_signal != "WAIT" and not cho_vao:
                        ai_signal = "WAIT"  # block signal khi không đồng pha
                else:
                    # Chỉ cấm ngược trend rõ ràng (D+H đồng pha)
                    if (trend == 1  and ai_signal == "SHORT") or (trend == -1 and ai_signal == "LONG"):
                        ai_signal = "WAIT"
                    cho_vao = ai_signal != "WAIT"

                # Không vào lệnh ngoài giờ giao dịch
                if not in_session:
                    cho_vao = False

                if ai_signal != "WAIT" and cho_vao:
                    sl = (current_price - initial_sl_pts if ai_signal == "LONG"
                          else current_price + initial_sl_pts)
                    st.session_state["ps_active_trade"] = {
                        "type": ai_signal, "entry": current_price,
                        "peak": current_price, "sl": sl, "entry_time": last_time,
                    }
                    icon = "🚀" if ai_signal == "LONG" else "🔻"
                    _send_telegram_async(
                        f"{icon} <b>#VN30F1M BÁO {ai_signal} MỚI</b>\n\n"
                        f"🎯 <b>Vùng vào lệnh:</b> {current_price}\n"
                        f"🛡️ <b>Cắt lỗ (SL):</b> {sl:.1f}\n"
                        f"⚡ <b>AI:</b> {conf:.1f}%\n"
                        f"🧭 <b>Đa khung:</b> {trend_text}\n"
                        f"<i>(Bot tự kéo SL theo giá)</i>"
                    )
                    log_entry = {
                        "time": last_time, "ticker": "VN30F1M",
                        "act":  f"BÁO {ai_signal}", "price": current_price,
                        "pnl":  "—", "reason": f"AI={conf:.1f}% | {trend_text}",
                    }
                    st.session_state["ps_log_history"].insert(0, log_entry)
                    _append_journal(log_entry)
                    _sync_gsheet_async(log_entry)

            st.session_state["ps_last_time"] = last_time

        except Exception as e:
            # FIX: Log lỗi ra session thay vì nuốt im lặng
            err_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {type(e).__name__}: {e}"
            errs = st.session_state["ps_errors"]
            errs.insert(0, err_msg)
            st.session_state["ps_errors"] = errs[:10]  # giữ 10 lỗi gần nhất

    elif _DATA_FILE_1M and not os.path.exists(_DATA_FILE_1M):
        st.info(f"📂 Đang chờ file `{_DATA_FILE_1M}` từ Amibroker...")

    # Hiện cảnh báo scaler out-of-range
    if st.session_state.get("ps_ai_warn"):
        st.warning(st.session_state["ps_ai_warn"])

    # ── Hiển thị lỗi trading (nếu có) ────────────────────────────────────────
    if st.session_state["ps_errors"]:
        with st.expander(f"🐛 Lỗi hệ thống ({len(st.session_state['ps_errors'])})", expanded=True):
            for err in st.session_state["ps_errors"]:
                st.code(err, language=None)
            if st.button("Xóa log lỗi", key="ps_clear_err"):
                st.session_state["ps_errors"] = []
                st.rerun()

    # ── Tín hiệu hành động ───────────────────────────────────────────────────
    st.subheader("🎯 Tín Hiệu Hành Động Hiện Tại")
    col1, col2, col3 = st.columns([1, 1.5, 1])

    col1.markdown(
        f"<div class='ps-box'>Giá Thị Trường<br>"
        f"<b style='font-size:28px'>{current_price:,.1f}</b><br>"
        f"<span style='font-size:12px;color:#9CA3AF'>{last_time or '—'}</span></div>",
        unsafe_allow_html=True,
    )

    active = st.session_state["ps_active_trade"]
    if active:
        cur_pnl = (
            (current_price - active["entry"]) if active["type"] == "LONG"
            else (active["entry"] - current_price)
        )
        clr     = "#00E676" if cur_pnl >= 0 else "#FF5252"
        box_cls = "ps-active" if cur_pnl >= 0 else "ps-warn"
        dir_cls = "c-long"   if active["type"] == "LONG" else "c-short"
        col2.markdown(
            f"<div class='ps-box {box_cls}'>TRẠNG THÁI<br>"
            f"<span class='{dir_cls}' style='font-size:20px'>ĐANG GIỮ {active['type']}</span><br>"
            f"Vào lệnh: <b>{active['entry']:,.1f}</b> &nbsp;|&nbsp; "
            f"Lãi/Lỗ: <b style='font-size:22px;color:{clr}'>{cur_pnl:+.1f} đ</b></div>",
            unsafe_allow_html=True,
        )
        col3.markdown(
            f"<div class='ps-box'>Trailing SL<br>"
            f"<b style='font-size:28px;color:#FFD600'>{active['sl']:.1f}</b><br>"
            f"<span style='font-size:12px;color:#9CA3AF'>AI {prob*100:.0f}% | {trend_text}</span></div>",
            unsafe_allow_html=True,
        )
    else:
        if ai_signal == "LONG":
            sig_color, sig_label = "#00E676", "🚀 LONG"
        elif ai_signal == "SHORT":
            sig_color, sig_label = "#FF5252", "🔻 SHORT"
        else:
            sig_color, sig_label = "#9CA3AF", "QUAN SÁT"
        col2.markdown(
            "<div class='ps-box'>TRẠNG THÁI<br>"
            "<b style='font-size:22px;color:#AAA'>ĐỨNG NGOÀI</b></div>",
            unsafe_allow_html=True,
        )
        col3.markdown(
            f"<div class='ps-box'>Dự báo AI<br>"
            f"<b style='font-size:20px;color:{sig_color}'>{sig_label}</b><br>"
            f"<span style='color:#9CA3AF;font-size:13px'>{prob*100:.1f}% Tăng</span><br>"
            f"<span style='color:#9CA3AF;font-size:12px'>{trend_text}</span></div>",
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Thống kê session ──────────────────────────────────────────────────────
    stats = _calc_session_stats(st.session_state["ps_log_history"])
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Số lệnh đóng",  stats["total"])
    s2.metric("Thắng / Thua",  f"{stats['wins']} / {stats['losses']}")
    s3.metric("Win Rate",       f"{stats['win_rate']:.0f}%")
    pnl_color = "normal" if stats["total_pnl"] >= 0 else "inverse"
    s4.metric("PnL session",    f"{stats['total_pnl']:+.1f} đ",
              delta_color=pnl_color)

    st.divider()

    # ── Nhật ký tín hiệu ─────────────────────────────────────────────────────
    st.subheader("📜 Nhật Ký Tín Hiệu (Session)")
    log = st.session_state["ps_log_history"]
    if log:
        rows = ""
        for item in log[:25]:
            act   = item["act"]
            a_cls = "c-long" if "LONG" in act else ("c-short" if "SHORT" in act else "")
            pnl   = str(item["pnl"])
            p_cls = "c-pos" if pnl.startswith("+") else ("c-neg" if pnl.startswith("-") and pnl != "—" else "")
            price = item["price"]
            p_str = f"{price:,.1f}" if isinstance(price, (int, float)) else str(price)
            rows += (
                f"<tr>"
                f"<td>{item['time']}</td>"
                f"<td class='{a_cls}'>{act}</td>"
                f"<td><b>{p_str}</b></td>"
                f"<td class='{p_cls}'>{pnl}</td>"
                f"<td>{item['reason']}</td>"
                f"</tr>"
            )
        st.markdown(
            f"<table class='ps-log'>"
            f"<tr><th>Thời gian</th><th>Tín hiệu</th>"
            f"<th>Giá</th><th>PnL (đ)</th><th>Ghi chú</th></tr>"
            f"{rows}</table>",
            unsafe_allow_html=True,
        )
    else:
        st.info("Hệ thống đang quan sát đồ thị. Chờ AI xuất tín hiệu đầu tiên...")

    # ── Journal từ file ───────────────────────────────────────────────────────
    if _JOURNAL_FILE and os.path.exists(_JOURNAL_FILE):
        with st.expander("📂 Lịch sử Journal (file CSV)", expanded=False):
            try:
                df_j = pd.read_csv(_JOURNAL_FILE)
                st.dataframe(df_j.tail(50).iloc[::-1], use_container_width=True, hide_index=True)
            except Exception:
                st.warning("Không đọc được file journal.")

    # ── Train LSTM ───────────────────────────────────────────────────────────
    st.divider()
    with st.expander("🧠 Train / Retrain Model LSTM", expanded=False):
        st.caption("Train model trên dữ liệu VN30F1M 1 phút. Chạy offline — không ảnh hưởng bot đang chạy.")
        t_c1, t_c2, t_c3 = st.columns(3)
        future_bars   = t_c1.number_input("Nhìn trước (nến)", 3, 30, 5, key="ps_train_future")
        profit_target = t_c2.number_input("Target lợi nhuận (điểm)", 0.5, 5.0, 1.0, 0.5, key="ps_train_target")
        epochs        = t_c3.number_input("Epochs tối đa", 10, 100, 30, 5, key="ps_train_epochs")

        data_ok = _DATA_FILE_1M and os.path.exists(_DATA_FILE_1M)
        if not data_ok:
            st.warning(f"⚠️ Chưa có file `vn30f1m_1min.csv`. Cần export từ Amibroker trước.")
        else:
            try:
                n_rows = sum(1 for _ in open(_DATA_FILE_1M)) - 1
                st.info(f"📊 File hiện có: **{n_rows:,} nến** | Model: {'✅ Có' if _MODEL_PATH and os.path.exists(_MODEL_PATH) else '❌ Chưa train'}")
            except Exception:
                pass

        if st.button("🚀 Bắt Đầu Train", disabled=not data_ok, key="ps_train_btn"):
            _run_lstm_training(
                data_file=_DATA_FILE_1M,
                model_file=_MODEL_PATH,
                scaler_file=_SCALER_PATH,
                future_bars=int(future_bars),
                profit_target=float(profit_target),
                epochs=int(epochs),
            )

    # ── Auto-refresh (cuối cùng) ──────────────────────────────────────────────
    if auto_refresh:
        time.sleep(1)
        st.rerun()
