"""
LSTM module — inference + feature engineering từ Amibroker history_by_ticker.

Nguồn data: C:\\AmibrokerData\\history_by_ticker\\{SYMBOL}.csv  (live, cập nhật từ Amibroker)
Model:      C:\\AmibrokerData\\stock_lstm_v6_multi.keras  (v6, 5 features)
            C:\\AmibrokerData\\stock_lstm_v7.keras         (v7, 10 features — sau khi train)

Dùng:
    from vn_invest.lstm import predict, model_ready, get_model_info
    result = predict("HPG")
    # {"ai_score": 62.3, "signal": "BUY-A", "risk": "Low", "phase": "Markup",
    #  "confidence_t5": 0.71, "confidence_t10": 0.65, "confidence_t25": 0.58,
    #  "rsi": 54.2, "dist_ema_pct": 2.1, "rows_used": 440, "model_version": "v6"}
"""
import logging
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

logger = logging.getLogger("vn_invest.lstm")

# ---------------------------------------------------------------------------
# Paths — ưu tiên v7 nếu có, fallback về v6
# ---------------------------------------------------------------------------
_AMI_DIR    = Path(os.getenv("AMIBROKER_HIST_DIR", r"C:\AmibrokerData\history_by_ticker"))
_V7_MODEL   = Path(os.getenv("LSTM_MODEL_PATH",   r"C:\AmibrokerData\stock_lstm_v7.keras"))
_V7_SCALER  = Path(os.getenv("LSTM_SCALER_PATH",  r"C:\AmibrokerData\stock_scaler_v7.pkl"))
_V6_MODEL   = Path(r"C:\AmibrokerData\stock_lstm_v6_multi.keras")
_V6_SCALER  = Path(r"C:\AmibrokerData\stock_scaler_v6.pkl")

SEQ_LEN     = 30

# Features cho từng version
_FEAT_V6 = ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change"]
_FEAT_V7 = ["RSI", "MACD_hist", "Dist_EMA", "Log_Ret", "Vol_Change",
            "ATR_norm", "RSI_slope5", "Vol_ratio20", "EMA_trend", "BB_pos"]

# ---------------------------------------------------------------------------
# Singleton — load model/scaler một lần duy nhất
# ---------------------------------------------------------------------------
_model   = None
_scaler  = None
_version = None


def _resolve_paths():
    """Trả (model_path, scaler_path, version, feat_cols)."""
    if _V7_MODEL.exists() and _V7_SCALER.exists():
        return _V7_MODEL, _V7_SCALER, "v7", _FEAT_V7
    if _V6_MODEL.exists() and _V6_SCALER.exists():
        return _V6_MODEL, _V6_SCALER, "v6", _FEAT_V6
    return None, None, None, None


def _load():
    global _model, _scaler, _version
    if _model is not None:
        return _model, _scaler, _version

    mpath, spath, ver, _ = _resolve_paths()
    if mpath is None:
        logger.warning("Không tìm thấy LSTM model tại C:\\AmibrokerData\\")
        return None, None, None

    try:
        from tensorflow.keras.models import load_model
        _scaler  = joblib.load(spath)
        _model   = load_model(str(mpath))
        _version = ver
        logger.info("LSTM %s loaded: %s", ver, mpath.name)
    except Exception as e:
        logger.error("Load model lỗi: %s", e)
        _model, _scaler, _version = None, None, None

    return _model, _scaler, _version


def model_ready() -> bool:
    m, s, v = _load()
    return m is not None


def get_model_info() -> dict:
    """Trả thông tin model đang dùng."""
    _, _, ver, feat_cols = _resolve_paths()
    return {
        "version":     ver or "none",
        "v7_exists":   _V7_MODEL.exists(),
        "v6_exists":   _V6_MODEL.exists(),
        "features":    feat_cols or [],
        "n_features":  len(feat_cols) if feat_cols else 0,
        "history_dir": str(_AMI_DIR),
        "tickers_available": len(list(_AMI_DIR.glob("*.csv"))) if _AMI_DIR.exists() else 0,
    }


# ---------------------------------------------------------------------------
# Đọc Amibroker CSV
# ---------------------------------------------------------------------------
def _parse_ami_date(date_val) -> str:
    """Amibroker date format '01YYMMDD' → 'YYYY-MM-DD'."""
    s = str(int(date_val)).zfill(8)
    return f"20{s[2:4]}-{s[4:6]}-{s[6:8]}"


def load_history(symbol: str) -> pd.DataFrame | None:
    """Đọc lịch sử giá từ Amibroker. Trả DataFrame: Date, Open, High, Low, Close, Volume."""
    path = _AMI_DIR / f"{symbol.upper()}.csv"
    if not path.exists():
        logger.warning("Không có file: %s", path.name)
        return None
    try:
        df = pd.read_csv(path, header=0)
        df.columns = [c.strip() for c in df.columns]
        df["Date"]   = pd.to_datetime(df["Date"].apply(_parse_ami_date), errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Close"])
    except Exception as e:
        logger.error("Lỗi đọc %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------
def _features_v6(df: pd.DataFrame) -> pd.DataFrame:
    """5 features cho model v6."""
    import pandas_ta as ta

    c, v = df["Close"], df["Volume"]
    rsi     = ta.rsi(c, 14).fillna(50)
    macd_df = ta.macd(c)
    macd    = (macd_df["MACD_12_26_9"].fillna(0) if macd_df is not None else pd.Series(0, index=df.index))
    ema34   = ta.ema(c, 34).fillna(c)
    dist    = ((c - ema34) / c).clip(-0.5, 0.5)
    log_ret = np.log(c / c.shift(1)).fillna(0)
    vol_chg = v.pct_change().fillna(0)

    return pd.DataFrame({
        "RSI": rsi, "MACD": macd,
        "Dist_EMA": dist, "Log_Ret": log_ret, "Vol_Change": vol_chg,
    }).fillna(0)


def _features_v7(df: pd.DataFrame) -> pd.DataFrame:
    """10 features cho model v7."""
    import pandas_ta as ta

    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    rsi     = ta.rsi(c, 14)
    macd_df = ta.macd(c, fast=12, slow=26, signal=9)
    macd_h  = macd_df["MACDh_12_26_9"] if macd_df is not None else pd.Series(0, index=df.index)
    ema34   = ta.ema(c, 34)
    dist    = ((c - ema34) / c).clip(-0.5, 0.5)
    log_ret = np.log(c / c.shift(1)).clip(-0.15, 0.15)
    vol_chg = v.pct_change().clip(-2, 5)
    atr     = ta.atr(h, l, c, 14)
    atr_n   = (atr / c).fillna(0)
    rsi_s5  = rsi - rsi.shift(5)
    vol_ma  = v.rolling(20).mean().replace(0, np.nan)
    vol_r   = (v / vol_ma).clip(0, 10)
    ema9    = ta.ema(c, 9); ema21 = ta.ema(c, 21)
    ema_tr  = ((ema9 - ema21) / c).fillna(0)
    bb = ta.bbands(c, length=20)
    if bb is not None and "BBU_20_2.0" in bb.columns:
        bw = bb["BBU_20_2.0"] - bb["BBL_20_2.0"]
        bb_p = ((c - bb["BBL_20_2.0"]) / bw.replace(0, np.nan)).clip(-0.5, 2.0)
    else:
        bb_p = pd.Series(0.5, index=df.index)

    feat = pd.DataFrame({
        "RSI": rsi, "MACD_hist": macd_h, "Dist_EMA": dist,
        "Log_Ret": log_ret, "Vol_Change": vol_chg, "ATR_norm": atr_n,
        "RSI_slope5": rsi_s5, "Vol_ratio20": vol_r, "EMA_trend": ema_tr, "BB_pos": bb_p,
    })
    return feat.ffill().fillna(0)


def compute_features(df: pd.DataFrame, version: str) -> pd.DataFrame:
    return _features_v7(df) if version == "v7" else _features_v6(df)


# ---------------------------------------------------------------------------
# Predict
# ---------------------------------------------------------------------------
def predict(symbol: str) -> dict | None:
    """
    Chạy LSTM inference cho 1 mã.

    Returns:
        {ai_score, signal, risk, phase,
         confidence_t5, confidence_t10, confidence_t25,
         rsi, dist_ema_pct, rows_used, model_version}
    Trả None nếu không đủ data hoặc model chưa load.
    """
    m, s, ver = _load()
    if m is None:
        return None

    _, _, _, feat_cols = _resolve_paths()

    df = load_history(symbol)
    if df is None or len(df) < SEQ_LEN + 10:
        return None

    feats = compute_features(df, ver)
    if len(feats) < SEQ_LEN:
        return None

    seq = feats[feat_cols].tail(SEQ_LEN).values
    if np.isnan(seq).any():
        return None

    try:
        seq_scaled = s.transform(seq)
    except Exception as e:
        logger.error("Scaler lỗi %s: %s", symbol, e)
        return None

    X = seq_scaled[np.newaxis, :, :]
    try:
        raw = m.predict(X, verbose=0)
    except Exception as e:
        logger.error("Predict lỗi %s: %s", symbol, e)
        return None

    # Xử lý output: v6 = list [t5, t10, t25], v7 tương tự
    if isinstance(raw, list):
        c_t5, c_t10, c_t25 = float(raw[0][0][0]), float(raw[1][0][0]), float(raw[2][0][0])
    elif hasattr(raw, "shape") and raw.ndim == 2 and raw.shape[1] == 3:
        c_t5, c_t10, c_t25 = float(raw[0, 0]), float(raw[0, 1]), float(raw[0, 2])
    else:
        # v6 output dạng single value (confidence)
        c_t5 = c_t10 = c_t25 = float(raw.flatten()[0])

    # ai_score = trung bình có trọng số (T+25 quan trọng nhất)
    ai_score = round((c_t5 * 0.25 + c_t10 * 0.35 + c_t25 * 0.40) * 100, 1)

    rsi_val     = float(feats["RSI"].iloc[-1])
    dist_ema    = float(feats["Dist_EMA"].iloc[-1]) * 100

    return {
        "ai_score":       ai_score,
        "signal":         _classify_signal(ai_score),
        "risk":           _classify_risk(ai_score, dist_ema),
        "phase":          _classify_phase(rsi_val, dist_ema),
        "confidence_t5":  round(c_t5, 3),
        "confidence_t10": round(c_t10, 3),
        "confidence_t25": round(c_t25, 3),
        "rsi":            round(rsi_val, 2),
        "dist_ema_pct":   round(dist_ema, 2),
        "rows_used":      len(df),
        "model_version":  ver,
    }


def batch_predict(symbols: list[str]) -> dict[str, dict]:
    """Predict cho nhiều mã. Trả {symbol: result_dict}."""
    results = {}
    for sym in symbols:
        r = predict(sym)
        if r:
            results[sym] = r
    return results


# ---------------------------------------------------------------------------
# Signal classifiers (calibrated từ backtest v6)
# ---------------------------------------------------------------------------
def _classify_signal(ai_score: float) -> str:
    if ai_score >= 50: return "BUY-A"
    if ai_score >= 40: return "BUY-B"
    if ai_score >= 30: return "HOLD"
    if ai_score >= 20: return "SELL-B"
    return "SELL-A"


def _classify_risk(ai_score: float, dist_ema_pct: float) -> str:
    if ai_score >= 50 and -5 <= dist_ema_pct <= 10: return "Low"
    if ai_score <= 20 and dist_ema_pct < -10:        return "High"
    return "Medium"


def _classify_phase(rsi: float, dist_ema_pct: float) -> str:
    if -5 <= dist_ema_pct <= 3 and 40 <= rsi <= 55: return "Accumulation"
    if dist_ema_pct > 3  and 55 <= rsi <= 70:        return "Markup"
    if dist_ema_pct > 3  and rsi > 65:               return "Distribution"
    if dist_ema_pct < -10 and rsi < 40:              return "Markdown"
    return "Neutral"
