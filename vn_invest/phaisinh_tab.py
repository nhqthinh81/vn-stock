"""Tab Phái Sinh — VN30F1M Signal Bot (Multi-TF + Trailing Stop).

Adapted from D:/phaisinh.py for multi-tab Streamlit integration:
- Removed while-loop; uses st.rerun() with auto-refresh toggle instead
- Session state prefixed 'ps_' to avoid conflicts with other tabs
- Telegram reads from .env (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID)
- Google Sheets optional: silent fail if credentials.json absent
"""
import os
import threading
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

# ── Data paths (try D:\ first, then C:\) ─────────────────────────────────────
_POTENTIAL_PATHS = [
    r"D:\AmibrokerData",
    r"C:\AmibrokerData",
    os.path.join(os.getcwd(), "AmibrokerData"),
]
_BASE_DATA_DIR = next((p for p in _POTENTIAL_PATHS if os.path.exists(p)), None)

if _BASE_DATA_DIR:
    _DATA_FILE_1M = os.path.join(_BASE_DATA_DIR, "data_feed.csv")
    _MODEL_PATH   = os.path.join(_BASE_DATA_DIR, "lstm_brain.keras")
    _SCALER_PATH  = os.path.join(_BASE_DATA_DIR, "lstm_scaler.pkl")
    _JOURNAL_FILE = os.path.join(_BASE_DATA_DIR, "vn30_ai_journal.csv")
    _CRED_FILE    = os.path.join(_BASE_DATA_DIR, "credentials.json")
else:
    _DATA_FILE_1M = _MODEL_PATH = _SCALER_PATH = _JOURNAL_FILE = _CRED_FILE = None

# ── Strategy constants ────────────────────────────────────────────────────────
_SEQ_LEN           = 30
_THRESHOLD_BUY     = 0.60
_THRESHOLD_SELL    = 0.40
_TRAILING_STOP_PTS = 3.0
_INITIAL_SL_PTS    = 3.0
_GSHEET_NAME       = "VN30_Trading_Journal"


@st.cache_resource
def _load_ai_system():
    """Load LSTM model + scaler once; cached across reruns."""
    if not (_SCALER_PATH and _MODEL_PATH and
            os.path.exists(_SCALER_PATH) and os.path.exists(_MODEL_PATH)):
        return None, None
    try:
        import joblib
        from tensorflow.keras.models import load_model  # type: ignore
        return joblib.load(_SCALER_PATH), load_model(_MODEL_PATH)
    except Exception:
        return None, None


# ── Feature / prediction helpers ─────────────────────────────────────────────

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
    except Exception:
        return None


def _calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    import pandas_ta as ta  # type: ignore
    df = df.copy()
    df["RSI"]       = ta.rsi(df["Close"], 14)
    macd            = ta.macd(df["Close"])
    df["MACD"]      = macd.iloc[:, 0] if macd is not None else 0
    df["EMA_34"]    = ta.ema(df["Close"], 34)
    df["Dist_EMA"]  = (df["Close"] - df["EMA_34"]) / df["Close"]
    df["Log_Ret"]   = np.log(df["Close"] / df["Close"].shift(1))
    df["Vol_Change"] = df["Volume"].pct_change()
    df.fillna(0, inplace=True)
    return df


def _get_ai_prediction(df_1m: pd.DataFrame, scaler, model) -> float:
    if scaler is None or model is None or len(df_1m) < _SEQ_LEN + 50:
        return 0.5
    try:
        df_f   = _calculate_features(df_1m.tail(150))
        cols   = ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change"]
        seq    = df_f[cols].tail(_SEQ_LEN).values
        scaled = scaler.transform(seq).reshape(1, _SEQ_LEN, len(cols))
        return float(model.predict(scaled, verbose=0)[0][0])
    except Exception:
        return 0.5


def _get_trend_from_1m(df_1m: pd.DataFrame):
    import pandas_ta as ta  # type: ignore
    if df_1m is None or len(df_1m) < 80:
        return 0, "Không đủ dữ liệu"

    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
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


# ── Telegram / journal helpers ────────────────────────────────────────────────

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
        mode   = "a" if os.path.exists(_JOURNAL_FILE) else "w"
        header = not os.path.exists(_JOURNAL_FILE)
        row.to_csv(_JOURNAL_FILE, mode=mode, header=header, index=False)
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


# ── Main render ───────────────────────────────────────────────────────────────

def render_phaisinh_tab():
    """Render toàn bộ tab Phái Sinh. Gọi từ app.py bên trong with tab_phaisinh:"""
    import time

    # CSS
    st.markdown("""
    <style>
        .ps-box    { background:#1E2129;padding:16px;border-radius:10px;text-align:center;border:1px solid #333; }
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

    # Session state defaults
    _defaults = {
        "ps_active_trade": None,
        "ps_log_history":  [],
        "ps_last_time":    "",
        "ps_last_mtime":   0,
        "ps_df_1m":        None,
    }
    for k, v in _defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    st.header("⚡ VN30F1M — Signal Bot (Multi-TF + Trailing Stop)")

    # No data directory
    if not _BASE_DATA_DIR:
        st.error(
            "❌ Không tìm thấy thư mục AmibrokerData tại D:\\ hoặc C:\\. "
            "Đảm bảo Amibroker đang xuất **data_feed.csv** vào đúng đường dẫn."
        )
        return

    # ── Controls bar ─────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns([1, 1, 3])
    auto_refresh = c1.toggle("🔄 Auto Refresh (1s)", value=False, key="ps_auto_toggle")
    if c2.button("🚨 Reset Khẩn Cấp", use_container_width=True):
        st.session_state["ps_active_trade"] = None
        st.success("Đã reset về trạng thái Quan sát!")
    c3.caption(
        f"Trailing: **{_TRAILING_STOP_PTS} đ** | "
        f"LONG khi AI > **{_THRESHOLD_BUY*100:.0f}%** | "
        f"SHORT khi AI < **{_THRESHOLD_SELL*100:.0f}%**"
    )

    st.divider()

    # ── Load AI (cached) ──────────────────────────────────────────────────────
    ai_scaler, ai_model = _load_ai_system()

    if ai_model is None:
        st.warning(
            f"⚠️ Chưa có model LSTM (`{_MODEL_PATH}`). "
            "Bot chạy ở chế độ đa khung, không có xác nhận AI."
        )

    # ── Read CSV only when Amibroker writes new data ──────────────────────────
    current_price = 0.0
    prob          = 0.5
    trend         = 0
    trend_text    = "Chưa xác định"
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

    if df_1m is not None and len(df_1m) > 0:
        try:
            current_price = float(df_1m.iloc[-1]["Close"])
            last_time     = df_1m.index[-1].strftime("%Y-%m-%d %H:%M")
            trend, trend_text = _get_trend_from_1m(df_1m)

            # A. Quản lý lệnh đang mở
            trade = st.session_state["ps_active_trade"]
            if trade is not None:
                exit_triggered, exit_reason = False, ""
                if last_time != trade["entry_time"]:
                    if trade["type"] == "LONG":
                        if current_price > trade["peak"]:
                            trade["peak"] = current_price
                            trade["sl"]   = max(trade["sl"], trade["peak"] - _TRAILING_STOP_PTS)
                        if current_price <= trade["sl"]:
                            exit_triggered, exit_reason = True, "Chạm Trailing Stop / SL"
                    elif trade["type"] == "SHORT":
                        if current_price < trade["peak"]:
                            trade["peak"] = current_price
                            trade["sl"]   = min(trade["sl"], trade["peak"] + _TRAILING_STOP_PTS)
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

            # B. Phát tín hiệu mới
            elif last_time != st.session_state["ps_last_time"]:
                if ai_model is not None:
                    prob = _get_ai_prediction(df_1m, ai_scaler, ai_model)
                conf = prob * 100 if prob >= 0.5 else (1 - prob) * 100

                if prob >= _THRESHOLD_BUY:
                    ai_signal = "LONG"
                elif prob <= _THRESHOLD_SELL:
                    ai_signal = "SHORT"

                # Filter: không trade ngược trend mạnh
                cho_vao = (
                    ai_signal != "WAIT"
                    and not (trend == 1  and ai_signal == "SHORT")
                    and not (trend == -1 and ai_signal == "LONG")
                )

                if cho_vao:
                    sl = (current_price - _INITIAL_SL_PTS if ai_signal == "LONG"
                          else current_price + _INITIAL_SL_PTS)
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

        except Exception:
            pass

    elif _DATA_FILE_1M and not os.path.exists(_DATA_FILE_1M):
        st.info(f"📂 Đang chờ file `{_DATA_FILE_1M}` từ Amibroker...")

    # ── Signal display ────────────────────────────────────────────────────────
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
            f"Vào lệnh: <b>{active['entry']:,.1f}</b><br>"
            f"Lãi/Lỗ: <b style='font-size:22px;color:{clr}'>{cur_pnl:+.1f} đ</b></div>",
            unsafe_allow_html=True,
        )
        col3.markdown(
            f"<div class='ps-box'>Trailing SL<br>"
            f"<b style='font-size:28px;color:#FFD600'>{active['sl']:.1f}</b></div>",
            unsafe_allow_html=True,
        )
    else:
        # Show AI probability / trend when flat
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

    # ── Signal log ────────────────────────────────────────────────────────────
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

    # ── Journal file history ──────────────────────────────────────────────────
    if _JOURNAL_FILE and os.path.exists(_JOURNAL_FILE):
        with st.expander("📂 Lịch sử Journal (file CSV)", expanded=False):
            try:
                df_j = pd.read_csv(_JOURNAL_FILE)
                st.dataframe(df_j.tail(50).iloc[::-1], use_container_width=True, hide_index=True)
            except Exception:
                st.warning("Không đọc được file journal.")

    # ── Auto-refresh (phải ở cuối để render xong trước khi rerun) ────────────
    if auto_refresh:
        time.sleep(1)
        st.rerun()
