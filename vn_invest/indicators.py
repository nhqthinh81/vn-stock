"""Tính chỉ báo kỹ thuật: RSI, MACD, EMA, SMA, tech_score, signal/risk/phase."""
import math

import numpy as np
import pandas as pd

from .config import RSI_OVERSOLD, RSI_OVERBOUGHT, SCORE_BUY_A, SCORE_BUY_B, SCORE_SELL_B, SCORE_SELL_A


# ── Chỉ báo cơ bản ──────────────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def calc_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Thêm tất cả chỉ báo vào DataFrame giá lịch sử."""
    df = df.copy()
    close = df["close"]

    df["rsi"] = calc_rsi(close)
    df["ema34"] = calc_ema(close, 34)
    df["sma20"] = calc_sma(close, 20)
    df["sma50"] = calc_sma(close, 50)

    macd_line, signal_line, histogram = calc_macd(close)
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = histogram

    df["dist_ema34_pct"] = (close - df["ema34"]) / df["ema34"] * 100
    df["log_return"] = np.log(close / close.shift(1))

    return df


# ── Phân loại tín hiệu ───────────────────────────────────────────────────────

def calculate_tech_score(rsi: float, macd_hist: float, dist_ema_pct: float) -> float:
    """Tính tech_score 0–100 từ 3 chỉ báo."""
    score = 50.0  # baseline

    # RSI component (±25 điểm)
    if rsi <= RSI_OVERSOLD:
        score += 25
    elif rsi >= RSI_OVERBOUGHT:
        score -= 25
    else:
        score += (RSI_OVERSOLD + RSI_OVERBOUGHT) / 2 - rsi

    # MACD histogram component (±15 điểm)
    if macd_hist > 0:
        score += min(15, macd_hist * 100)
    else:
        score += max(-15, macd_hist * 100)

    # Dist EMA34 component (±10 điểm)
    if dist_ema_pct < -5:
        score += 10   # giá dưới EMA34 nhiều → cơ hội mua
    elif dist_ema_pct > 10:
        score -= 10   # giá trên EMA34 nhiều → quá mua

    return max(0.0, min(100.0, score))


def classify_signal(tech_score: float) -> str:
    if tech_score >= SCORE_BUY_A:
        return "BUY-A"
    elif tech_score >= SCORE_BUY_B:
        return "BUY-B"
    elif tech_score >= SCORE_SELL_B:
        return "HOLD"
    elif tech_score >= SCORE_SELL_A:
        return "SELL-B"
    else:
        return "SELL-A"


def classify_risk(tech_score: float, dist_ema_pct: float) -> str:
    if tech_score >= SCORE_BUY_B and dist_ema_pct < 5:
        return "Low"
    elif tech_score >= SCORE_SELL_B:
        return "Medium"
    else:
        return "High"


def classify_phase(rsi: float, dist_ema_pct: float) -> str:
    if rsi < 40 and dist_ema_pct < -3:
        return "Markdown"
    elif rsi < 50 and dist_ema_pct < 3:
        return "Accumulation"
    elif rsi > 60 and dist_ema_pct > 3:
        return "Distribution"
    elif rsi > 50 and dist_ema_pct > 0:
        return "Markup"
    else:
        return "Neutral"


def get_latest_signals(df: pd.DataFrame) -> dict:
    """Trả dict tín hiệu mới nhất từ DataFrame đã có indicators."""
    last = df.dropna(subset=["rsi", "macd_hist", "dist_ema34_pct"]).iloc[-1]
    rsi = float(last["rsi"])
    macd_hist = float(last["macd_hist"])
    dist_ema = float(last["dist_ema34_pct"])
    close = float(last["close"])
    log_ret = float(last["log_return"]) if not math.isnan(last["log_return"]) else 0.0

    tech_score = calculate_tech_score(rsi, macd_hist, dist_ema)
    return {
        "close": close,
        "rsi": round(rsi, 2),
        "macd_hist": round(macd_hist, 4),
        "dist_ema34_pct": round(dist_ema, 2),
        "log_return": round(log_ret, 4),
        "tech_score": round(tech_score, 1),
        "signal": classify_signal(tech_score),
        "risk": classify_risk(tech_score, dist_ema),
        "phase": classify_phase(rsi, dist_ema),
    }
