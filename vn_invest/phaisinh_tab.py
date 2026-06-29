"""Tab Phái Sinh — VN30F1M Signal Bot v3 (Rule-based + LSTM Ensemble).

v3 vs v2:
- Rule-based engine: không phụ thuộc LSTM (LSTM accuracy 44% quá kém).
  LONG: RSI<35 + MACD histogram đảo chiều dương + Close > VWAP
  SHORT: RSI>65 + MACD histogram đảo chiều âm + Close < VWAP + trend==-1
- Chế độ hoạt động: Rule-only / LSTM-only / Ensemble (cả 2 đồng thuận)
- Take-Profit target: tự chốt lời khi đạt TP (ngoài trailing stop)
- Risk/Reward display: TP/SL ratio hiện thị khi vào lệnh

v2 vs v1:
- Label 3 class (0=SHORT / 1=WAIT / 2=LONG): loại bỏ lỗi "label=0 = SHORT"
- Trend filter: SHORT chỉ khi trend==-1
- Daily trend: tail(20000) + loại nến ngày chưa hoàn thành
- Features: 8 features (thêm VWAP_Dist, Session_Gap, ATR_Norm)
- Training: class_weight balanced, sparse_categorical_crossentropy
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

# ── Hằng số ──────────────────────────────────────────────────────────────────
_SEQ_LEN                 = 30
_DEFAULT_THRESHOLD_BUY   = 0.55   # prob_long >= 55% → LONG
_DEFAULT_THRESHOLD_SELL  = 0.55   # prob_short >= 55% → SHORT
_DEFAULT_TRAILING_PTS    = 3.0
_DEFAULT_INITIAL_SL_PTS  = 3.0
_DEFAULT_TP_PTS          = 5.0   # Take-profit mặc định (0 = tắt)
_GSHEET_NAME             = "VN30_Trading_Journal"

# Features v2 (8 features) — KHÔNG thay đổi thứ tự (scaler phụ thuộc)
_FEATURES = ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change",
             "VWAP_Dist", "Session_Gap", "ATR_Norm"]

# ── Giờ giao dịch VN30F1M ────────────────────────────────────────────────────
_SESSION1_START        = dtime(9, 0)
_SESSION1_END          = dtime(11, 30)
_SESSION2_START        = dtime(13, 0)
_SESSION2_END          = dtime(14, 45)
_WARN_BEFORE_CLOSE_MIN = 5


def _in_trading_session(now: dtime | None = None) -> tuple[bool, str]:
    if now is None:
        now = datetime.now().time()
    if _SESSION1_START <= now <= _SESSION1_END:
        dt_now  = datetime.combine(datetime.today(), now)
        dt_end1 = datetime.combine(datetime.today(), _SESSION1_END)
        mins_left = (dt_end1 - dt_now).seconds // 60
        if mins_left <= _WARN_BEFORE_CLOSE_MIN:
            return True, f"⚠️ Phiên 1 đóng sau {mins_left} phút"
        return True, f"🟢 Phiên 1 (09:00–11:30)"
    if _SESSION2_START <= now <= _SESSION2_END:
        dt_now  = datetime.combine(datetime.today(), now)
        dt_end2 = datetime.combine(datetime.today(), _SESSION2_END)
        mins_left = (dt_end2 - dt_now).seconds // 60
        if mins_left <= _WARN_BEFORE_CLOSE_MIN:
            return True, f"⚠️ Phiên 2 đóng sau {mins_left} phút"
        return True, f"🟢 Phiên 2 (13:00–14:45)"
    if now < _SESSION1_START:
        return False, "⏳ Chờ mở phiên 1 (09:00)"
    if _SESSION1_END < now < _SESSION2_START:
        return False, "⏸ Nghỉ trưa (11:30–13:00)"
    return False, "🔴 Ngoài giờ giao dịch (>14:45)"


# ── Load AI model ─────────────────────────────────────────────────────────────
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
        return None, str(e)


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
        df = df.dropna(subset=["Close"])
        df.sort_index(inplace=True)
        # Chỉ giữ 25000 nến gần nhất — đủ ~66 ngày, giảm memory + tốc độ downstream
        return df.tail(25000).copy()
    except Exception:
        return None


def _calc_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP theo từng ngày giao dịch (reset về 0 mỗi ngày)."""
    d = df.copy()
    d["_date"]    = d.index.date
    d["_typical"] = (d["High"] + d["Low"] + d["Close"]) / 3
    d["_tpv"]     = d["_typical"] * d["Volume"].clip(lower=0)
    cum_tpv = d.groupby("_date")["_tpv"].cumsum()
    cum_vol = d.groupby("_date")["Volume"].cumsum().clip(lower=1)
    return cum_tpv / cum_vol


def _calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """Tính 8 features v2. Drop warmup rows — KHÔNG fillna(0) trừ hàng đầu."""
    try:
        import pandas_ta as ta  # type: ignore
    except ImportError:
        raise ImportError("pip install pandas_ta")

    df = df.copy()

    # --- Core indicators ---
    df["RSI"]      = ta.rsi(df["Close"], 14)
    _macd          = ta.macd(df["Close"])
    df["MACD"]     = _macd.iloc[:, 0] if _macd is not None else np.nan
    df["EMA_34"]   = ta.ema(df["Close"], 34)
    df["Dist_EMA"] = (df["Close"] - df["EMA_34"]) / df["Close"].clip(lower=1)
    df["Log_Ret"]  = np.log(df["Close"] / df["Close"].shift(1))
    df["Vol_Change"] = df["Volume"].pct_change().clip(-5, 5)

    # --- ATR normalized ---
    _atr = ta.atr(df["High"], df["Low"], df["Close"], 14)
    df["ATR_Norm"] = (_atr / df["Close"].clip(lower=1)).fillna(0)

    # --- VWAP distance (per-session, reset daily) ---
    vwap = _calc_vwap(df)
    df["VWAP_Dist"] = (df["Close"] - vwap) / df["Close"].clip(lower=1)

    # --- Session gap: % thay đổi từ giá mở cửa phiên hôm nay ---
    df["_date"]        = df.index.date
    df["Session_Open"] = df.groupby("_date")["Open"].transform("first")
    df["Session_Gap"]  = (df["Close"] - df["Session_Open"]) / df["Session_Open"].clip(lower=1)

    # Drop warmup NaN (RSI cần 14 bars, MACD cần 26, EMA34 cần 34)
    df.dropna(subset=["RSI", "MACD", "EMA_34"], inplace=True)
    # Hàng đầu tiên sau warmup có thể còn NaN ở Log_Ret/Vol_Change
    df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]] = (
        df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]].fillna(0)
    )
    return df


def _get_ai_prediction(df_1m: pd.DataFrame, scaler, model) -> tuple[float, float, str | None]:
    """Trả (prob_long, prob_short, warning).

    Model v2 (3-class): output [p_short, p_wait, p_long]
    Model v1 (binary):  output [p_long] — SHORT bị block hoàn toàn.
    """
    if scaler is None or model is None or len(df_1m) < _SEQ_LEN + 60:
        return 0.5, 0.0, None
    try:
        df_f = _calculate_features(df_1m.tail(300))
        if len(df_f) < _SEQ_LEN:
            return 0.5, 0.0, "Không đủ hàng sau drop warmup"

        # Dùng features có trong df_f (hỗ trợ cả model v1 với 5 features)
        is_v2 = hasattr(scaler, "n_features_in_") and scaler.n_features_in_ == len(_FEATURES)
        feat_cols = _FEATURES if is_v2 else ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change"]
        available = [c for c in feat_cols if c in df_f.columns]
        if len(available) < len(feat_cols):
            return 0.5, 0.0, f"Thiếu features: {set(feat_cols) - set(available)}"

        seq = df_f[available].tail(_SEQ_LEN).values

        # Kiểm tra out-of-range
        warn = None
        if hasattr(scaler, "data_min_") and hasattr(scaler, "data_max_"):
            lo, hi = scaler.data_min_, scaler.data_max_
            if seq.shape[1] == len(lo) and (np.any(seq < lo * 0.7) or np.any(seq > hi * 1.3)):
                warn = "⚠️ Feature nằm ngoài range lúc train — model có thể không chính xác"

        scaled = scaler.transform(seq).reshape(1, _SEQ_LEN, len(available))
        pred   = model.predict(scaled, verbose=0)[0]

        n_out = model.output_shape[-1]
        if n_out == 3:
            # v2: [p_short, p_wait, p_long]
            prob_short = float(pred[0])
            prob_long  = float(pred[2])
        else:
            # v1 binary: p_long only. KHÔNG sinh SHORT từ model cũ (bug label).
            prob_long  = float(pred[0])
            prob_short = 0.0
            if warn is None:
                warn = "ℹ️ Model v1 (binary) — SHORT bị tắt. Retrain để dùng model v2."

        return prob_long, prob_short, warn

    except Exception as e:
        return 0.5, 0.0, f"Lỗi AI: {e}"


def _get_rule_signal(df_1m: pd.DataFrame, trend: int) -> tuple[str, str]:
    """Rule-based signal engine v3 — đa tín hiệu, ngưỡng 2.5/5.0.

    Bảng điểm (LONG / SHORT đối xứng):
      RSI < 40 / > 60        → +2.0  (oversold/overbought rõ)
      RSI < 48 / > 52        → +1.0  (lệch khỏi trung tính)
      MACD zero-cross ↑/↓    → +1.5  (histogram vừa đổi dấu — mạnh nhất)
      MACD histogram cải thiện→ +1.0  (histogram đang tăng/giảm)
      EMA5 cross EMA10 ↑/↓   → +1.0  (xác nhận momentum ngắn hạn)
      Giá trên/dưới VWAP     → +0.5
      Trend thuận chiều       → +0.5
    Ngưỡng vào lệnh: ≥ 2.5
    """
    try:
        import pandas_ta as ta  # type: ignore
    except ImportError:
        return "WAIT", "Thiếu pandas_ta"

    if df_1m is None or len(df_1m) < 60:
        return "WAIT", "Chưa đủ dữ liệu"

    try:
        recent = df_1m.tail(200).copy()
        recent["RSI"]       = ta.rsi(recent["Close"], 14)
        _macd               = ta.macd(recent["Close"])
        recent["MACD_hist"] = _macd.iloc[:, 2] if _macd is not None and _macd.shape[1] >= 3 else np.nan
        recent["EMA5"]      = ta.ema(recent["Close"], 5)
        recent["EMA10"]     = ta.ema(recent["Close"], 10)
        recent["VWAP"]      = _calc_vwap(recent)
        recent.dropna(subset=["RSI", "MACD_hist", "EMA5", "EMA10"], inplace=True)

        if len(recent) < 3:
            return "WAIT", "Chưa đủ sau warmup"

        last  = recent.iloc[-1]
        prev  = recent.iloc[-2]
        rsi   = float(last["RSI"])
        close = float(last["Close"])
        mh    = float(last["MACD_hist"])
        mh_p  = float(prev["MACD_hist"])
        e5    = float(last["EMA5"])
        e10   = float(last["EMA10"])
        e5_p  = float(prev["EMA5"])
        e10_p = float(prev["EMA10"])
        vwap  = float(last["VWAP"]) if not np.isnan(last.get("VWAP", np.nan)) else None

        # ── LONG score ────────────────────────────────────────────────────────
        ls, lp = 0.0, []

        if rsi < 40:   ls += 2.0; lp.append(f"RSI={rsi:.1f}<40(+2)")
        elif rsi < 48: ls += 1.0; lp.append(f"RSI={rsi:.1f}<48(+1)")

        if mh > 0 and mh_p <= 0:   ls += 1.5; lp.append(f"MACD zero-cross↑(+1.5)")
        elif mh > mh_p:             ls += 1.0; lp.append(f"MACD↑{mh:.2f}(+1)")

        if e5 > e10 and e5_p <= e10_p: ls += 1.0; lp.append("EMA5×EMA10↑(+1)")

        if vwap and close > vwap:   ls += 0.5; lp.append("↑VWAP(+0.5)")
        if trend >= 0:              ls += 0.5; lp.append(f"trend={'↑' if trend==1 else '→'}(+0.5)")

        # ── SHORT score ───────────────────────────────────────────────────────
        ss, sp = 0.0, []

        if rsi > 60:   ss += 2.0; sp.append(f"RSI={rsi:.1f}>60(+2)")
        elif rsi > 52: ss += 1.0; sp.append(f"RSI={rsi:.1f}>52(+1)")

        if mh < 0 and mh_p >= 0:   ss += 1.5; sp.append(f"MACD zero-cross↓(+1.5)")
        elif mh < mh_p:             ss += 1.0; sp.append(f"MACD↓{mh:.2f}(+1)")

        if e5 < e10 and e5_p >= e10_p: ss += 1.0; sp.append("EMA5×EMA10↓(+1)")

        if vwap and close < vwap:   ss += 0.5; sp.append("↓VWAP(+0.5)")
        if trend <= 0:              ss += 0.5; sp.append(f"trend={'↓' if trend==-1 else '→'}(+0.5)")

        # ── Quyết định (ngưỡng 2.5) ──────────────────────────────────────────
        _THRESHOLD = 2.5
        if ls >= _THRESHOLD and ls > ss:
            return "LONG",  f"Score={ls:.1f} | " + " | ".join(lp)
        if ss >= _THRESHOLD and ss > ls:
            return "SHORT", f"Score={ss:.1f} | " + " | ".join(sp)
        if ls >= _THRESHOLD:
            return "LONG",  f"Score={ls:.1f} | " + " | ".join(lp)
        if ss >= _THRESHOLD:
            return "SHORT", f"Score={ss:.1f} | " + " | ".join(sp)

        best = "LONG" if ls >= ss else "SHORT"
        best_s = ls if best == "LONG" else ss
        return "WAIT", (
            f"Chờ: {best} {best_s:.1f}/{_THRESHOLD} | "
            f"RSI={rsi:.1f} MACD={'↑' if mh>mh_p else '↓'}{mh:.2f} "
            f"EMA={'↑' if e5>e10 else '↓'} trend={'↑' if trend==1 else '↓' if trend==-1 else '→'}"
        )

    except Exception as e:
        return "WAIT", f"Lỗi rule engine: {e}"


def _get_trend_full(df_1m: pd.DataFrame) -> tuple[int, str, dict]:
    """Multi-TF trend + chi tiết từng khung — một lần resample duy nhất.

    Trả về: (trend: int, trend_text: str, tf_detail: dict)
    - trend: -1/0/1
    - tf_detail: {"1m": {...}, "15m": {...}, "1h": {...}}
    """
    empty_tf = {"trend": 0, "ema": None, "close": None, "label": "—"}
    tf_detail = {"1m": empty_tf.copy(), "15m": empty_tf.copy(), "1h": empty_tf.copy()}

    try:
        import pandas_ta as ta  # type: ignore
    except ImportError:
        return 0, "Thiếu pandas_ta", tf_detail

    if df_1m is None or len(df_1m) < 200:
        return 0, "Không đủ dữ liệu", tf_detail

    agg    = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    # df_1m đã bị giới hạn tail(25000) khi load → không cần tail() lại
    recent = df_1m

    # Resample một lần, dùng chung cho trend + tf_detail
    df_15 = recent.resample("15min").agg(agg).dropna()
    df_1h = recent.resample("60min").agg(agg).dropna()
    df_d  = recent.resample("1D").agg(agg).dropna()

    # Loại nến ngày chưa hoàn thành
    today = pd.Timestamp.now().normalize()
    df_d  = df_d[df_d.index < today]

    # ── tf_detail: 1m ─────────────────────────────────────────────────────────
    _1m_df = recent.tail(200).copy()
    _1m_df["EMA10"] = ta.ema(_1m_df["Close"], 10)
    _1m_df.dropna(subset=["EMA10"], inplace=True)
    if not _1m_df.empty:
        last = _1m_df.iloc[-1]
        t1m  = 1 if last["Close"] > last["EMA10"] else -1
        tf_detail["1m"] = {"trend": t1m, "ema": float(last["EMA10"]),
                            "close": float(last["Close"]),
                            "label": "↑ Tăng" if t1m == 1 else "↓ Giảm"}

    # ── tf_detail: 15m ────────────────────────────────────────────────────────
    if not df_15.empty:
        _15m = df_15.copy()
        _15m["EMA10"] = ta.ema(_15m["Close"], 10)
        _15m.dropna(subset=["EMA10"], inplace=True)
        if not _15m.empty:
            last = _15m.iloc[-1]
            t15  = 1 if last["Close"] > last["EMA10"] else -1
            tf_detail["15m"] = {"trend": t15, "ema": float(last["EMA10"]),
                                 "close": float(last["Close"]),
                                 "label": "↑ Tăng" if t15 == 1 else "↓ Giảm"}

    # ── tf_detail: 1h ─────────────────────────────────────────────────────────
    if not df_1h.empty:
        _1h = df_1h.copy()
        _1h["EMA20"] = ta.ema(_1h["Close"], 20)
        _1h.dropna(subset=["EMA20"], inplace=True)
        if not _1h.empty:
            last = _1h.iloc[-1]
            t1h  = 1 if last["Close"] > last["EMA20"] else -1
            tf_detail["1h"] = {"trend": t1h, "ema": float(last["EMA20"]),
                                "close": float(last["Close"]),
                                "label": "↑ Tăng" if t1h == 1 else "↓ Giảm"}

    # ── Multi-TF trend score ──────────────────────────────────────────────────
    if len(df_15) < 20 or len(df_1h) < 10 or len(df_d) < 25:
        missing = []
        if len(df_d)  < 25: missing.append(f"daily={len(df_d)}<25")
        if len(df_1h) < 10: missing.append(f"1h={len(df_1h)}<10")
        if len(df_15) < 20: missing.append(f"15m={len(df_15)}<20")
        return 0, f"Đang gom dữ liệu đa khung ({', '.join(missing)})", tf_detail

    _d  = df_d.copy();  _d["EMA20"]  = ta.ema(_d["Close"], 20);  _d.dropna(subset=["EMA20"],  inplace=True)
    _1h = df_1h.copy(); _1h["EMA20"] = ta.ema(_1h["Close"], 20); _1h.dropna(subset=["EMA20"], inplace=True)
    _15 = df_15.copy(); _15["EMA10"] = ta.ema(_15["Close"], 10); _15.dropna(subset=["EMA10"], inplace=True)

    if not (_d.shape[0] and _1h.shape[0] and _15.shape[0]):
        return 0, "EMA chưa đủ nến warmup", tf_detail

    t_d  = 1 if _d.iloc[-1]["Close"]  > _d.iloc[-1]["EMA20"]  else -1
    t_1h = 1 if _1h.iloc[-1]["Close"] > _1h.iloc[-1]["EMA20"] else -1
    t_15 = 1 if _15.iloc[-1]["Close"] > _15.iloc[-1]["EMA10"] else -1
    score = t_d + t_1h + t_15

    if score ==  3: return  1, "UPTREND mạnh (D/H/15p đồng pha tăng)", tf_detail
    if score ==  2: return  1, "UPTREND (D+H tăng, 15p nhiễu)",        tf_detail
    if score ==  1: return  0, "Trung tính thiên tăng",                 tf_detail
    if score == -1: return  0, "Trung tính thiên giảm",                 tf_detail
    if score == -2: return -1, "DOWNTREND (D+H giảm, 15p nhiễu)",      tf_detail
    if score == -3: return -1, "DOWNTREND mạnh (D/H/15p đồng pha giảm)", tf_detail
    return 0, "Xu hướng trung tính", tf_detail


def _get_stop_levels(df_1m: pd.DataFrame) -> dict:
    """Tính Buy Stop / Sell Stop từ pivot gần nhất (10 nến) + SL đề xuất.

    - Entry: vượt đỉnh/đáy 10 nến gần nhất + buffer nhỏ (0.2×ATR, tối thiểu 0.5đ)
    - SL Buy Stop  = đáy thấp nhất 10 nến - buffer  (rủi ro ≈ 0.4×ATR)
    - SL Sell Stop = đỉnh cao nhất 10 nến + buffer
    Thiết kế cho scalping 1m — vùng chặt, R/R thường 1:1.5+
    """
    empty = {"buy_stop_price": None, "buy_stop_sl": None,
             "sell_stop_price": None, "sell_stop_sl": None, "atr": None}
    if df_1m is None or len(df_1m) < 20:
        return empty
    try:
        import pandas_ta as ta  # type: ignore
        recent = df_1m.tail(500).copy()
        atr_s = ta.atr(recent["High"], recent["Low"], recent["Close"], length=14)
        if atr_s is None or atr_s.dropna().empty:
            return empty
        atr = float(atr_s.dropna().iloc[-1])
        buf = max(round(atr * 0.2, 1), 0.5)   # buffer vào lệnh: 0.2×ATR, tối thiểu 0.5đ
        sl_buf = max(round(atr * 0.2, 1), 0.5) # buffer SL: tương tự

        w = recent.tail(10)
        pivot_high = float(w["High"].max())
        pivot_low  = float(w["Low"].min())

        return {
            "buy_stop_price":  round(pivot_high + buf, 1),
            "buy_stop_sl":     round(pivot_low  - sl_buf, 1),
            "sell_stop_price": round(pivot_low  - buf, 1),
            "sell_stop_sl":    round(pivot_high + sl_buf, 1),
            "atr":             round(atr, 1),
        }
    except Exception:
        return empty


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


# ── Thống kê session ──────────────────────────────────────────────────────────

def _calc_session_stats(log: list[dict]) -> dict:
    closed = [x for x in log if x["act"].startswith("ĐÓNG")]
    if not closed:
        return {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "win_rate": 0.0}
    total_pnl = wins = losses = 0.0
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
        "total":     int(n),
        "wins":      int(wins),
        "losses":    int(losses),
        "total_pnl": total_pnl,
        "win_rate":  (wins / n * 100) if n else 0.0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# LSTM Training v2 — 3-class label + class_weight balanced
# ══════════════════════════════════════════════════════════════════════════════

def _run_lstm_training(data_file, model_file, scaler_file,
                       future_bars=5, profit_target=1.0, epochs=30):
    """Train LSTM 3-class trong UI. Label: 0=SHORT / 1=WAIT / 2=LONG."""
    SEQ_LEN    = 30
    BATCH_SIZE = 64
    TEST_RATIO = 0.15

    try:
        import pandas_ta as ta
        import joblib
        from sklearn.preprocessing import MinMaxScaler
        from sklearn.metrics import classification_report
        from sklearn.utils.class_weight import compute_class_weight
        import tensorflow as tf
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization
        from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    except ImportError as e:
        st.error(f"❌ Thiếu thư viện: {e}")
        return

    with st.status("🧠 Đang train model LSTM v2...", expanded=True) as status:

        # 1. Load & parse
        st.write("📂 Đọc dữ liệu...")
        df = pd.read_csv(data_file)
        df.index = pd.to_datetime(df["Date"] + " " + df["Time"], dayfirst=True)
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.dropna(subset=["Close"], inplace=True)
        df.sort_index(inplace=True)
        st.write(f"✅ {len(df):,} nến | {df.index[0].date()} → {df.index[-1].date()}")

        # 2. Features v2 (8 features)
        st.write("⚙️ Tính 8 features (RSI, MACD, EMA34, LogRet, Vol, VWAP, Gap, ATR)...")
        df["RSI"]        = ta.rsi(df["Close"], 14)
        _macd            = ta.macd(df["Close"])
        df["MACD"]       = _macd.iloc[:, 0] if _macd is not None else np.nan
        df["EMA_34"]     = ta.ema(df["Close"], 34)
        df["Dist_EMA"]   = (df["Close"] - df["EMA_34"]) / df["Close"].clip(lower=1)
        df["Log_Ret"]    = np.log(df["Close"] / df["Close"].shift(1))
        df["Vol_Change"] = df["Volume"].pct_change().clip(-5, 5)
        _atr             = ta.atr(df["High"], df["Low"], df["Close"], 14)
        df["ATR_Norm"]   = (_atr / df["Close"].clip(lower=1))
        vwap             = _calc_vwap(df)
        df["VWAP_Dist"]  = (df["Close"] - vwap) / df["Close"].clip(lower=1)
        df["_date"]      = df.index.date
        df["Session_Open"] = df.groupby("_date")["Open"].transform("first")
        df["Session_Gap"]  = (df["Close"] - df["Session_Open"]) / df["Session_Open"].clip(lower=1)

        # 3. Label 3-class (FIX CHÍNH)
        st.write(f"🏷️ Tạo nhãn 3 class (target={profit_target} điểm, nhìn trước {future_bars} nến)...")
        df["future_close"] = df["Close"].shift(-future_bars)
        df.dropna(subset=["RSI", "MACD", "EMA_34", "future_close"], inplace=True)
        df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]] = (
            df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]].fillna(0)
        )

        diff = df["future_close"] - df["Close"]
        df["label"] = 1  # WAIT (default)
        df.loc[diff >=  profit_target, "label"] = 2   # LONG
        df.loc[diff <= -profit_target, "label"] = 0   # SHORT

        label_counts = df["label"].value_counts().sort_index()
        st.write(
            f"✅ {len(df):,} nến sau clean | "
            f"SHORT={label_counts.get(0,0):,} ({label_counts.get(0,0)/len(df)*100:.1f}%) | "
            f"WAIT={label_counts.get(1,0):,} ({label_counts.get(1,0)/len(df)*100:.1f}%) | "
            f"LONG={label_counts.get(2,0):,} ({label_counts.get(2,0)/len(df)*100:.1f}%)"
        )

        # 4. Sequences
        st.write("🔢 Tạo sequences...")
        X_data = df[_FEATURES].values
        y_data = df["label"].values
        X_seqs, y_seqs = [], []
        for i in range(SEQ_LEN, len(X_data)):
            X_seqs.append(X_data[i - SEQ_LEN:i])
            y_seqs.append(y_data[i])
        X_seqs = np.array(X_seqs, dtype=np.float32)
        y_seqs = np.array(y_seqs, dtype=np.int32)

        # 5. Scale + split
        st.write("📐 Scale + train/test split...")
        split   = int(len(X_seqs) * (1 - TEST_RATIO))
        X_train, X_test = X_seqs[:split], X_seqs[split:]
        y_train, y_test = y_seqs[:split], y_seqs[split:]

        scaler = MinMaxScaler()
        X_flat_train = X_train.reshape(-1, len(_FEATURES))
        X_flat_test  = X_test.reshape(-1, len(_FEATURES))
        X_train = scaler.fit_transform(X_flat_train).reshape(-1, SEQ_LEN, len(_FEATURES))
        X_test  = scaler.transform(X_flat_test).reshape(-1, SEQ_LEN, len(_FEATURES))
        joblib.dump(scaler, scaler_file)
        st.write(f"✅ Train: {len(X_train):,} | Test: {len(X_test):,}")

        # 6. Class weights balanced (FIX: chống bias về WAIT)
        st.write("⚖️ Tính class weights (balanced)...")
        cw_arr = compute_class_weight("balanced", classes=np.array([0, 1, 2]), y=y_train)
        class_weight = {0: float(cw_arr[0]), 1: float(cw_arr[1]), 2: float(cw_arr[2])}
        st.write(f"   SHORT={class_weight[0]:.2f} | WAIT={class_weight[1]:.2f} | LONG={class_weight[2]:.2f}")

        # 7. Build model v2 (softmax 3 outputs)
        st.write("🏗️ Build model LSTM v2 (3-class softmax)...")
        model = Sequential([
            LSTM(64, input_shape=(SEQ_LEN, len(_FEATURES)), return_sequences=True),
            Dropout(0.2),
            BatchNormalization(),
            LSTM(32, return_sequences=False),
            Dropout(0.2),
            Dense(16, activation="relu"),
            Dense(3, activation="softmax"),   # 3 outputs: SHORT / WAIT / LONG
        ])
        model.compile(
            optimizer="adam",
            loss="sparse_categorical_crossentropy",
            metrics=["accuracy"],
        )

        # 8. Train
        st.write(f"🚀 Train ({epochs} epochs max, early stopping patience=5)...")
        progress_bar = st.progress(0)

        class _StreamlitCallback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                pct = int((epoch + 1) / epochs * 100)
                progress_bar.progress(
                    pct,
                    text=f"Epoch {epoch+1}/{epochs} | loss={logs.get('loss',0):.4f} | "
                         f"val_acc={logs.get('val_accuracy',0):.3f}"
                )

        model.fit(
            X_train, y_train,
            epochs=epochs,
            batch_size=BATCH_SIZE,
            validation_data=(X_test, y_test),
            class_weight=class_weight,
            callbacks=[
                EarlyStopping(patience=5, restore_best_weights=True, monitor="val_loss"),
                ReduceLROnPlateau(patience=3, factor=0.5, monitor="val_loss"),
                _StreamlitCallback(),
            ],
            verbose=0,
        )

        # 9. Evaluate
        st.write("📊 Đánh giá...")
        _, acc = model.evaluate(X_test, y_test, verbose=0)
        y_pred = np.argmax(model.predict(X_test, verbose=0), axis=1)
        report = classification_report(y_test, y_pred,
                                       target_names=["SHORT", "WAIT", "LONG"],
                                       output_dict=True)

        model.save(model_file)
        status.update(label=f"✅ Train xong! Accuracy: {acc*100:.1f}%", state="complete")

    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("Test Accuracy",    f"{acc*100:.1f}%")
    r2.metric("LONG Precision",   f"{report['LONG']['precision']*100:.1f}%")
    r3.metric("LONG Recall",      f"{report['LONG']['recall']*100:.1f}%")
    r4.metric("SHORT Precision",  f"{report['SHORT']['precision']*100:.1f}%")
    r5.metric("SHORT Recall",     f"{report['SHORT']['recall']*100:.1f}%")
    st.success(f"✅ Model v2 lưu: `{model_file}` | Scaler: `{scaler_file}`\nRestart app để load model mới.")
    st.cache_resource.clear()


# ── 2 fragment: run_every phải cố định lúc khai báo nên cần 2 hàm riêng ──────

@st.fragment(run_every=1)
def _live_panel_auto():
    _live_panel_body()


@st.fragment
def _live_panel_manual():
    _live_panel_body()


# ══════════════════════════════════════════════════════════════════════════════
# Main render
# ══════════════════════════════════════════════════════════════════════════════

def render_phaisinh_tab():
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
        .c-wait  { color:#9CA3AF;font-weight:bold }
        .c-pos   { color:#00B0FF;font-weight:bold }
        .c-neg   { color:#FFD600;font-weight:bold }
        .prob-bar { height:10px;border-radius:5px;margin:3px 0; }
    </style>
    """, unsafe_allow_html=True)

    # ── Session state defaults ────────────────────────────────────────────────
    _defaults = {
        "ps_log_history":     [],
        "ps_last_time":       "",
        "ps_last_mtime":      0,
        "ps_df_1m":           None,
        "ps_last_prob_long":  0.33,
        "ps_last_prob_short": 0.33,
        "ps_last_trend":      (0, "—"),
        "ps_tf_detail":       {"1m": {}, "15m": {}, "1h": {}},
        "ps_trend_mtime":     -1,
        "ps_signal_audit":    [],
        "ps_errors":          [],
        "ps_ai_warn":         None,
        "ps_rule_signal":     "WAIT",
        "ps_rule_reason":     "—",
        "ps_rule_mtime":      -1,
        "ps_stop_levels":     {},   # Buy Stop / Sell Stop gợi ý
    }
    for k, v in _defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    st.header("⚡ VN30F1M — Signal Bot v3 (Rule-based + Ensemble)")

    if not _BASE_DATA_DIR:
        st.error(
            "❌ Không tìm thấy thư mục AmibrokerData tại D:\\ hoặc C:\\. "
            "Đảm bảo Amibroker đang xuất vn30f1m_1min.csv vào đúng đường dẫn."
        )
        return

    # ── Controls ──────────────────────────────────────────────────────────────
    with st.expander("⚙️ Cấu hình chiến thuật", expanded=False):
        cfg_c1, cfg_c2 = st.columns(2)
        thr_long  = cfg_c1.slider("Ngưỡng LONG LSTM (%)",  50, 90, int(_DEFAULT_THRESHOLD_BUY  * 100), key="ps_thr_buy")  / 100

        mode_col, strict_col = st.columns([2, 1])
        signal_mode = mode_col.radio(
            "Chế độ tín hiệu",
            options=["Rule-based", "LSTM", "Ensemble (cả 2 đồng thuận)"],
            index=0, horizontal=True, key="ps_signal_mode",
            help="Rule-based: RSI+MACD+VWAP. LSTM: model AI. Ensemble: cần cả 2 cùng chiều.",
        )
        strict_trend = strict_col.checkbox(
            "Strict trend (3 khung)",
            value=False, key="ps_strict_trend",
            help="Yêu cầu cả D/H/15p đồng pha mới vào lệnh",
        )
        thr_short = _DEFAULT_THRESHOLD_SELL   # short threshold (LSTM mode)

    top_c1, top_c2, top_c3 = st.columns([1, 1, 2])
    top_c1.toggle("🔄 Auto Refresh (1s)", value=False, key="ps_auto_toggle")
    if top_c2.button("🔄 Xóa log lỗi", use_container_width=True):
        st.session_state["ps_errors"] = []
        st.rerun()

    _, session_msg = _in_trading_session()
    top_c3.caption(session_msg)

    st.divider()

    # ── Chọn fragment đúng theo trạng thái toggle ─────────────────────────────
    # run_every phải cố định lúc khai báo decorator nên cần 2 hàm riêng.
    # Fragment chỉ rerun vùng bên trong — app.py và các tab khác không bị block.
    if st.session_state.get("ps_auto_toggle", False):
        _live_panel_auto()
    else:
        _live_panel_manual()


# ── Nội dung live panel (dùng chung cho cả 2 fragment) ───────────────────────
def _live_panel_body():
    """Logic signal + hiển thị UI — được gọi từ cả 2 fragment (auto/manual)."""
    # Đọc config từ session_state (widgets đã khai báo key= trong render_phaisinh_tab)
    thr_long    = st.session_state.get("ps_thr_buy",      _DEFAULT_THRESHOLD_BUY)
    thr_short   = _DEFAULT_THRESHOLD_SELL
    signal_mode = st.session_state.get("ps_signal_mode",  "Rule-based")
    strict_trend = st.session_state.get("ps_strict_trend", False)
    in_session, _ = _in_trading_session()

    # ── Load AI ───────────────────────────────────────────────────────────────
    ai_result = _load_ai_system()
    if isinstance(ai_result[1], str) and ai_result[0] is None:
        st.error(f"❌ Lỗi load model: {ai_result[1]}")
        ai_scaler, ai_model = None, None
    else:
        ai_scaler, ai_model = ai_result

    if ai_model is None:
        st.warning(f"⚠️ Chưa có model (`{_MODEL_PATH}`). Train model mới bên dưới.")
    else:
        n_out = ai_model.output_shape[-1]
        ver   = "v2 (3-class)" if n_out == 3 else "v1 (binary — SHORT bị tắt, cần retrain)"
        col_mv, _ = st.columns([3, 1])
        col_mv.caption(f"🧠 Model: **{ver}** | Features: {ai_scaler.n_features_in_ if hasattr(ai_scaler,'n_features_in_') else '?'}")

    # ── Đọc CSV khi mtime thay đổi ───────────────────────────────────────────
    current_price = 0.0
    last_time     = st.session_state["ps_last_time"]
    ai_signal     = "WAIT"
    rule_sig      = st.session_state.get("ps_rule_signal", "WAIT")
    prob_long     = st.session_state["ps_last_prob_long"]
    prob_short    = st.session_state["ps_last_prob_short"]

    try:
        cur_mtime = os.path.getmtime(_DATA_FILE_1M)
        if cur_mtime != st.session_state["ps_last_mtime"]:
            st.session_state["ps_df_1m"]      = _load_df_1m(_DATA_FILE_1M)
            st.session_state["ps_last_mtime"] = cur_mtime
    except Exception:
        pass

    df_1m = st.session_state["ps_df_1m"]

    # ── Cache trend (tính lại khi mtime đổi) ─────────────────────────────────
    cur_mtime_val = st.session_state["ps_last_mtime"]
    if cur_mtime_val != st.session_state["ps_trend_mtime"] and df_1m is not None:
        trend, trend_text, tf_detail = _get_trend_full(df_1m)
        st.session_state["ps_last_trend"]  = (trend, trend_text)
        st.session_state["ps_tf_detail"]   = tf_detail
        st.session_state["ps_trend_mtime"] = cur_mtime_val
    else:
        trend, trend_text = st.session_state["ps_last_trend"]

    # ── Logic trading ─────────────────────────────────────────────────────────
    if df_1m is not None and len(df_1m) > 0:
        try:
            current_price = float(df_1m.iloc[-1]["Close"])
            last_time     = df_1m.index[-1].strftime("%Y-%m-%d %H:%M")

            # ── Tính tín hiệu mỗi candle mới ────────────────────────────────
            if last_time != st.session_state["ps_last_time"]:
                # Rule-based signal (chỉ tính 1 lần khi có nến mới)
                rule_sig, rule_reason = _get_rule_signal(df_1m, trend)
                st.session_state["ps_rule_signal"] = rule_sig
                st.session_state["ps_rule_reason"] = rule_reason
                st.session_state["ps_rule_mtime"]  = cur_mtime_val

                # Buy Stop / Sell Stop levels
                st.session_state["ps_stop_levels"] = _get_stop_levels(df_1m)

                # Audit log
                audit = st.session_state.setdefault("ps_signal_audit", [])
                audit.insert(0, {
                    "time": last_time, "price": current_price,
                    "signal": rule_sig, "reason": rule_reason, "trend": trend,
                })
                st.session_state["ps_signal_audit"] = audit[:50]

                # LSTM (nếu có model)
                lstm_sig = "WAIT"
                if ai_model is not None:
                    prob_long_new, prob_short_new, ai_warn = _get_ai_prediction(df_1m, ai_scaler, ai_model)
                    prob_long  = prob_long_new
                    prob_short = prob_short_new
                    st.session_state["ps_last_prob_long"]  = prob_long
                    st.session_state["ps_last_prob_short"] = prob_short
                    st.session_state["ps_ai_warn"]         = ai_warn
                    if prob_long >= thr_long:   lstm_sig = "LONG"
                    elif prob_short >= thr_short: lstm_sig = "SHORT"
                    if strict_trend:
                        if lstm_sig == "LONG"  and trend != 1:  lstm_sig = "WAIT"
                        if lstm_sig == "SHORT" and trend != -1: lstm_sig = "WAIT"
                    else:
                        if lstm_sig == "SHORT" and trend != -1: lstm_sig = "WAIT"
                        if lstm_sig == "LONG"  and trend == -1: lstm_sig = "WAIT"

                # Tổng hợp
                if signal_mode == "Rule-based":
                    ai_signal   = rule_sig
                    signal_desc = rule_reason
                elif signal_mode == "LSTM":
                    ai_signal   = lstm_sig
                    signal_desc = f"LSTM L={prob_long*100:.0f}% S={prob_short*100:.0f}%"
                else:
                    if rule_sig != "WAIT" and rule_sig == lstm_sig:
                        ai_signal   = rule_sig
                        signal_desc = f"Ensemble✓ {rule_reason}"
                    else:
                        ai_signal   = "WAIT"
                        signal_desc = f"Ensemble: Rule={rule_sig} LSTM={lstm_sig}"

                if not in_session:
                    ai_signal = "WAIT"

                # Gửi Telegram khi có tín hiệu (chỉ thông báo, không tự vào lệnh)
                if ai_signal != "WAIT":
                    sl_ref  = st.session_state["ps_stop_levels"]
                    tp_pts  = st.session_state.get("ps_tp_pts", _DEFAULT_TP_PTS)
                    icon    = "🚀" if ai_signal == "LONG" else "🔻"
                    if ai_signal == "LONG":
                        sl_price   = sl_ref.get("buy_stop_sl")
                        stop_price = sl_ref.get("buy_stop_price")
                        tp_price   = round(current_price + tp_pts, 1) if tp_pts else None
                    else:
                        sl_price   = sl_ref.get("sell_stop_sl")
                        stop_price = sl_ref.get("sell_stop_price")
                        tp_price   = round(current_price - tp_pts, 1) if tp_pts else None
                    stop_line = f"📌 Stop lệnh: {stop_price:.1f}\n" if stop_price else ""
                    sl_line   = f"🛡️ SL: {sl_price:.1f}\n"  if sl_price else ""
                    tp_line   = f"🎯 TP: {tp_price:.1f}\n"  if tp_price else ""
                    _send_telegram_async(
                        f"{icon} <b>#VN30F1M TÍN HIỆU {ai_signal}</b>\n"
                        f"💰 Giá hiện tại: {current_price:.1f}\n"
                        f"{stop_line}{sl_line}{tp_line}"
                        f"⚡ {signal_desc}\n"
                        f"🧭 {trend_text}\n"
                        f"⚠️ Chỉ tham khảo — tự quyết định vào lệnh"
                    )
                    log_entry = {
                        "time":   last_time, "ticker": "VN30F1M",
                        "act":    f"TÍN HIỆU {ai_signal}", "price": current_price,
                        "sl":     sl_price,   "tp": tp_price,
                        "pnl":    "—",        "reason": signal_desc,
                    }
                    st.session_state["ps_log_history"].insert(0, log_entry)
                    _append_journal(log_entry)

            st.session_state["ps_last_time"] = last_time

        except Exception as e:
            err_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {type(e).__name__}: {e}"
            errs = st.session_state["ps_errors"]
            errs.insert(0, err_msg)
            st.session_state["ps_errors"] = errs[:10]

    elif _DATA_FILE_1M and not os.path.exists(_DATA_FILE_1M):
        st.info(f"📂 Đang chờ file `{_DATA_FILE_1M}` từ Amibroker...")

    # ── Cảnh báo data stale (AFL không cập nhật) ─────────────────────────────
    if df_1m is not None and in_session:
        try:
            last_candle_dt = df_1m.index[-1]
            stale_mins = (pd.Timestamp.now() - last_candle_dt).total_seconds() / 60
            if stale_mins > 3:
                st.warning(
                    f"⚠️ **Dữ liệu cũ {stale_mins:.0f} phút** — AFL có thể chưa chạy Explorer. "
                    f"Candle gần nhất: `{last_candle_dt.strftime('%H:%M')}`"
                )
        except Exception:
            pass

    if st.session_state.get("ps_ai_warn"):
        st.warning(st.session_state["ps_ai_warn"])

    if st.session_state["ps_errors"]:
        with st.expander(f"🐛 Lỗi hệ thống ({len(st.session_state['ps_errors'])})", expanded=True):
            for err in st.session_state["ps_errors"]:
                st.code(err, language=None)
            if st.button("Xóa log lỗi", key="ps_clear_err"):
                st.session_state["ps_errors"] = []
                st.rerun()

    # ── Panel chẩn đoán tín hiệu — 20 candle gần nhất ───────────────────────
    audit_log = st.session_state.get("ps_signal_audit", [])
    if audit_log:
        with st.expander(f"🔍 Chẩn đoán tín hiệu — {len(audit_log)} candle gần nhất", expanded=False):
            _sig_colors = {"LONG": "#00E676", "SHORT": "#FF5252", "WAIT": "#555"}
            rows_html = ""
            for a in audit_log[:20]:
                clr  = _sig_colors.get(a["signal"], "#555")
                tdot = "↑" if a["trend"] == 1 else "↓" if a["trend"] == -1 else "→"
                rows_html += (
                    f"<tr>"
                    f"<td style='color:#888;font-size:11px;padding:2px 6px'>{a['time'][-5:]}</td>"
                    f"<td style='color:#ccc;font-size:11px;padding:2px 6px'>{a['price']:,.1f}</td>"
                    f"<td style='color:{clr};font-weight:700;font-size:11px;padding:2px 6px'>{a['signal']}</td>"
                    f"<td style='color:#888;font-size:10px;padding:2px 6px'>{tdot} {a['reason'][:80]}</td>"
                    f"</tr>"
                )
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse'>"
                f"<thead><tr>"
                f"<th style='color:#666;font-size:10px;text-align:left;padding:2px 6px'>Giờ</th>"
                f"<th style='color:#666;font-size:10px;text-align:left;padding:2px 6px'>Giá</th>"
                f"<th style='color:#666;font-size:10px;text-align:left;padding:2px 6px'>Signal</th>"
                f"<th style='color:#666;font-size:10px;text-align:left;padding:2px 6px'>Lý do / Score</th>"
                f"</tr></thead><tbody>{rows_html}</tbody></table>",
                unsafe_allow_html=True,
            )

    # ── Hiển thị tín hiệu ────────────────────────────────────────────────────
    st.subheader("🎯 Tín Hiệu Hành Động")
    col1, col2, col3 = st.columns([1, 1.5, 1])

    col1.markdown(
        f"<div class='ps-box'>Giá thị trường<br>"
        f"<b style='font-size:28px'>{current_price:,.1f}</b><br>"
        f"<span style='font-size:11px;color:#9CA3AF'>{last_time or '—'}</span><br>"
        f"<span style='font-size:11px;color:#9CA3AF'>{trend_text}</span></div>",
        unsafe_allow_html=True,
    )

    # Trạng thái tín hiệu hiện tại
    if rule_sig == "LONG":
        sig_color, sig_label = "#00E676", "🚀 LONG"
    elif rule_sig == "SHORT":
        sig_color, sig_label = "#FF5252", "🔻 SHORT"
    else:
        sig_color, sig_label = "#9CA3AF", "⏸ QUAN SÁT"

    trend_color = "#00E676" if trend == 1 else ("#FF5252" if trend == -1 else "#888")
    trend_badge = f'<span style="color:{trend_color};font-size:11px">{trend_text}</span>'

    col2.markdown(
        f"<div class='ps-box'>TÍN HIỆU<br>"
        f"<b style='font-size:22px;color:{sig_color}'>{sig_label}</b><br>"
        f"{trend_badge}</div>",
        unsafe_allow_html=True,
    )

    # Prob bar AI (nếu có model)
    prob_wait = max(0.0, 1.0 - prob_long - prob_short)
    bar_short = f"{prob_short*100:.1f}"
    bar_wait  = f"{prob_wait*100:.1f}"
    bar_long  = f"{prob_long*100:.1f}"
    col3.markdown(
        f"<div class='ps-box'>Dự báo AI<br>"
        f"<div style='display:flex;height:10px;border-radius:5px;overflow:hidden;margin:4px 0'>"
        f"<div style='width:{bar_short}%;background:#FF5252'></div>"
        f"<div style='width:{bar_wait}%;background:#444'></div>"
        f"<div style='width:{bar_long}%;background:#00E676'></div>"
        f"</div>"
        f"<span style='color:#FF5252;font-size:11px'>S={bar_short}%</span>&nbsp;"
        f"<span style='color:#888;font-size:11px'>W={bar_wait}%</span>&nbsp;"
        f"<span style='color:#00E676;font-size:11px'>L={bar_long}%</span></div>",
        unsafe_allow_html=True,
    )

    # ── Trend 3 khung timeframe ───────────────────────────────────────────────
    tf_detail = st.session_state.get("ps_tf_detail", {})
    if tf_detail:
        tf_c1, tf_c2, tf_c3 = st.columns(3)
        for col_ui, tf_key, tf_label in [
            (tf_c1, "1m",  "🕐 1 Phút"),
            (tf_c2, "15m", "🕒 15 Phút"),
            (tf_c3, "1h",  "🕐 1 Giờ"),
        ]:
            info = tf_detail.get(tf_key, {})
            t    = info.get("trend", 0)
            lbl  = info.get("label", "—")
            ema  = info.get("ema")
            cls  = info.get("close")
            clr  = "#00E676" if t == 1 else ("#FF5252" if t == -1 else "#888")
            ema_str = f"<br><span style='font-size:10px;color:#666'>EMA={ema:,.1f} | C={cls:,.1f}</span>" if ema else ""
            col_ui.markdown(
                f"<div style='background:#111;border:1px solid #2a2a2a;border-left:3px solid {clr};"
                f"border-radius:6px;padding:7px 10px;text-align:center'>"
                f"<div style='font-size:10px;color:#666;margin-bottom:2px'>{tf_label}</div>"
                f"<div style='font-size:14px;font-weight:700;color:{clr}'>{lbl}</div>"
                f"{ema_str}</div>",
                unsafe_allow_html=True,
            )

    # ── 4 card tín hiệu: LONG | SHORT | BUY STOP | SELL STOP ───────────────────
    rule_sig    = st.session_state.get("ps_rule_signal", "WAIT")
    rule_reason = st.session_state.get("ps_rule_reason", "—")
    stop_lvl    = st.session_state.get("ps_stop_levels", {})

    buy_stop_px  = stop_lvl.get("buy_stop_price")
    buy_stop_sl  = stop_lvl.get("buy_stop_sl")
    sell_stop_px = stop_lvl.get("sell_stop_price")
    sell_stop_sl = stop_lvl.get("sell_stop_sl")
    atr_val      = stop_lvl.get("atr")

    def _sig_card(icon, label, value_html, bg, border):
        return (
            f"<div style='background:{bg};border:2px solid {border};border-radius:10px;"
            f"padding:10px 12px;text-align:center'>"
            f"<div style='font-size:15px;font-weight:bold;color:{border}'>{icon} {label}</div>"
            f"<div style='font-size:13px;color:#ddd;margin-top:4px'>{value_html}</div>"
            f"</div>"
        )

    def _stop_card(icon, label, entry_px, sl_px, atr, bg, border):
        if entry_px is None:
            body = "<span style='color:#666'>Đang tính...</span>"
        else:
            rr_pts = abs(entry_px - sl_px) if sl_px else 0
            body = (
                f"<span style='font-size:13px;color:#aaa'>Vào lệnh</span><br>"
                f"<b style='font-size:20px;color:{border}'>{entry_px:.1f}</b><br>"
                f"<span style='font-size:12px;color:#FF6B6B'>SL: {sl_px:.1f}</span>"
                f"<span style='font-size:11px;color:#666'> ({rr_pts:.1f}đ)</span>"
                + (f"<br><span style='font-size:10px;color:#555'>ATR={atr}</span>" if atr else "")
            )
        return (
            f"<div style='background:{bg};border:2px solid {border};border-radius:10px;"
            f"padding:10px 12px;text-align:center'>"
            f"<div style='font-size:15px;font-weight:bold;color:{border}'>{icon} {label}</div>"
            f"<div style='margin-top:4px'>{body}</div>"
            f"</div>"
        )

    long_active  = rule_sig == "LONG"
    short_active = rule_sig == "SHORT"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_sig_card(
            "🚀", "LONG (MP)",
            rule_reason if long_active else "<span style='color:#555'>Chưa có tín hiệu</span>",
            "#0d2b1a" if long_active else "#111", "#00E676" if long_active else "#444",
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_sig_card(
            "🔻", "SHORT (MP)",
            rule_reason if short_active else "<span style='color:#555'>Chưa có tín hiệu</span>",
            "#2b0d0d" if short_active else "#111", "#FF5252" if short_active else "#444",
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_stop_card("⬆️", "BUY STOP", buy_stop_px, buy_stop_sl, atr_val, "#0d1a2b", "#29B6F6"), unsafe_allow_html=True)
    with c4:
        st.markdown(_stop_card("⬇️", "SELL STOP", sell_stop_px, sell_stop_sl, atr_val, "#1a0d2b", "#CE93D8"), unsafe_allow_html=True)

    if ai_model is not None:
        lstm_color = "#00E676" if prob_long > prob_short and prob_long > 0.4 else ("#FF5252" if prob_short > prob_long and prob_short > 0.4 else "#9CA3AF")
        st.markdown(
            f"<div style='font-size:11px;color:#888;margin-top:4px'>LSTM: "
            f"<span style='color:{lstm_color}'>L={prob_long*100:.0f}% S={prob_short*100:.0f}%</span></div>",
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Thống kê session ──────────────────────────────────────────────────────
    stats = _calc_session_stats(st.session_state["ps_log_history"])
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Số lệnh đóng",  stats["total"])
    s2.metric("Thắng / Thua",  f"{stats['wins']} / {stats['losses']}")
    s3.metric("Win Rate",       f"{stats['win_rate']:.0f}%")
    s4.metric("PnL session",    f"{stats['total_pnl']:+.1f}đ",
              delta_color="normal" if stats["total_pnl"] >= 0 else "inverse")

    st.divider()

    # ── Nhật ký ──────────────────────────────────────────────────────────────
    st.subheader("📜 Nhật Ký Tín Hiệu (Session)")
    log = st.session_state["ps_log_history"]
    if log:
        rows = ""
        for item in log[:25]:
            act   = item["act"]
            a_cls = "c-long" if "LONG" in act else ("c-short" if "SHORT" in act else "c-wait")
            pnl   = str(item["pnl"])
            p_cls = "c-pos" if pnl.startswith("+") else ("c-neg" if (pnl.startswith("-") and pnl != "—") else "")
            p_str = f"{item['price']:,.1f}" if isinstance(item["price"], (int, float)) else str(item["price"])
            sl_v  = item.get("sl")
            tp_v  = item.get("tp")
            sl_str = f"<span style='color:#FF5252'>{sl_v:.1f}</span>" if sl_v else "—"
            tp_str = f"<span style='color:#00E676'>{tp_v:.1f}</span>" if tp_v else "—"
            rows += (
                f"<tr><td>{item['time']}</td>"
                f"<td class='{a_cls}'>{act}</td>"
                f"<td><b>{p_str}</b></td>"
                f"<td>{sl_str}</td>"
                f"<td>{tp_str}</td>"
                f"<td class='{p_cls}'>{pnl}</td>"
                f"<td style='font-size:.82rem;color:#888'>{item['reason']}</td></tr>"
            )
        st.markdown(
            f"<table class='ps-log'>"
            f"<tr><th>Thời gian</th><th>Tín hiệu</th><th>Giá vào</th>"
            f"<th style='color:#FF5252'>SL</th><th style='color:#00E676'>TP</th>"
            f"<th>PnL (đ)</th><th>Lý do</th></tr>"
            f"{rows}</table>",
            unsafe_allow_html=True,
        )
    else:
        st.info("Hệ thống đang quan sát. Chờ AI xuất tín hiệu đầu tiên...")

    # ── Journal CSV ───────────────────────────────────────────────────────────
    if _JOURNAL_FILE and os.path.exists(_JOURNAL_FILE):
        with st.expander("📂 Lịch sử Journal (file CSV)", expanded=False):
            try:
                df_j = pd.read_csv(_JOURNAL_FILE)
                st.dataframe(df_j.tail(50).iloc[::-1], use_container_width=True, hide_index=True)
            except Exception:
                st.warning("Không đọc được file journal.")

    # ── Train LSTM v2 ─────────────────────────────────────────────────────────
    st.divider()
    with st.expander("🧠 Train / Retrain Model LSTM v2 (3-class)", expanded=False):
        st.caption(
            "**v2**: Label 3 class (SHORT/WAIT/LONG), class_weight balanced, 8 features. "
            "Model cũ (v1 binary) vẫn dùng được nhưng bị block SHORT — nên retrain."
        )
        st.info(
            "💡 **Khuyến nghị:** `Nhìn trước=10, Target=0.5` → nhiều label LONG/SHORT hơn "
            "(v1 default: 5/1.0 → chỉ 15% label không phải WAIT → LSTM bias về WAIT). "
            "Nếu kết quả LONG Recall < 30% → tăng `Nhìn trước` hoặc giảm `Target`."
        )
        t_c1, t_c2, t_c3 = st.columns(3)
        future_bars   = t_c1.number_input("Nhìn trước (nến 1min)", 3, 30, 10, key="ps_train_future")
        profit_target = t_c2.number_input("Target (điểm)", 0.3, 5.0, 0.5, 0.1, key="ps_train_target")
        epochs        = t_c3.number_input("Epochs tối đa", 10, 100, 30, 5, key="ps_train_epochs")

        data_ok = _DATA_FILE_1M and os.path.exists(_DATA_FILE_1M)
        if not data_ok:
            st.warning(f"⚠️ Chưa có file `vn30f1m_1min.csv`. Export từ Amibroker trước.")
        else:
            try:
                n_rows = sum(1 for _ in open(_DATA_FILE_1M)) - 1
                model_info = "❌ Chưa train"
                if _MODEL_PATH and os.path.exists(_MODEL_PATH):
                    _sc, _mdl = ai_scaler, ai_model
                    if _mdl is not None:
                        n_out = _mdl.output_shape[-1]
                        model_info = f"✅ v2 (3-class)" if n_out == 3 else "⚠️ v1 binary (SHORT bị block)"
                st.info(f"📊 Data: **{n_rows:,} nến** | Model: {model_info}")
            except Exception:
                pass

        if st.button("🚀 Bắt Đầu Train v2", disabled=not data_ok, key="ps_train_btn", type="primary"):
            _run_lstm_training(
                data_file=_DATA_FILE_1M,
                model_file=_MODEL_PATH,
                scaler_file=_SCALER_PATH,
                future_bars=int(future_bars),
                profit_target=float(profit_target),
                epochs=int(epochs),
            )

    # Auto-refresh được xử lý bởi @st.fragment(run_every=1) — không cần sleep/rerun ở đây
