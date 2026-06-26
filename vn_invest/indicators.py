"""Tính chỉ báo kỹ thuật: RSI, MACD, EMA, SMA, tech_score, signal/risk/phase."""
import math

import numpy as np
import pandas as pd

from .config import RSI_OVERSOLD, RSI_OVERBOUGHT, SCORE_BUY_A, SCORE_BUY_B, SCORE_SELL_B, SCORE_SELL_A


# ── Chỉ báo cơ bản ──────────────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    # Wilder's Smoothed Moving Average (alpha=1/period), đúng với định nghĩa gốc RSI-1978
    # SMA gây dao động rộng hơn và lag khác — không tương thích với ngưỡng 30/70 chuẩn
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
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


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — Wilder (1978).
    True Range = max(H-L, |H-Cprev|, |L-Cprev|)
    ATR = RMA(TR, period)  [Wilder smoothing = EMA với alpha=1/period]
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder smoothing (alpha = 1/period) ≡ EMA với adjust=False, com=period-1
    return tr.ewm(com=period - 1, adjust=False).mean()


def calc_bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0):
    """Bollinger Bands — John Bollinger (1983).
    Trả (upper, mid, lower, width_pct)
    width_pct = (upper - lower) / mid × 100  → đo độ nén/giãn của giá
    """
    mid   = series.rolling(window).mean()
    std   = series.rolling(window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width_pct = (upper - lower) / mid * 100
    return upper, mid, lower, width_pct


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

    # MACD cross freshness — số bars kể từ khi histogram đổi dấu lần gần nhất
    # Bar 1 = ngay sau cross, bar 999 = chưa có cross hoặc đã rất lâu
    _sign = np.sign(df["macd_hist"].fillna(0))
    _changed = (_sign != _sign.shift(1)).astype(int)
    _changed.iloc[0] = 1   # bar đầu tiên luôn coi là "cross mới" (không có prev)
    _group = _changed.cumsum()
    df["macd_bars_since_cross"] = _group.groupby(_group).cumcount() + 1

    # ATR-based volatility (Wilder 1978) — yêu cầu cột high/low
    if {"high", "low"}.issubset(df.columns):
        df["atr"] = calc_atr(df)
        df["atr_pct"] = df["atr"] / close * 100   # ATR tương đối (% giá)
    else:
        df["atr"] = np.nan
        df["atr_pct"] = np.nan

    # Bollinger Band Width (Bollinger 1983)
    bb_upper, bb_mid, bb_lower, bb_width = calc_bollinger(close)
    df["bb_upper"]    = bb_upper
    df["bb_mid"]      = bb_mid
    df["bb_lower"]    = bb_lower
    df["bb_width_pct"] = bb_width   # % width → squeeze khi thấp, expansion khi cao

    # Volume Ratio: volume hiện tại / SMA20(volume) — xác nhận breakout
    if "volume" in df.columns:
        vol_ma20 = df["volume"].rolling(20).mean()
        df["volume_ratio"] = df["volume"] / vol_ma20.replace(0, np.nan)
    else:
        df["volume_ratio"] = np.nan

    # MA Alignment Score: đo cấu trúc xu hướng (SMA20 vs SMA50 vs close)
    # +2: close>SMA20>SMA50 — bull aligned hoàn toàn
    # +1: SMA20>SMA50 nhưng close<SMA20 — uptrend nhưng pullback
    #  0: SMA20 ≈ SMA50 (within 1%) — sideways
    # -1: SMA20<SMA50 nhưng close>SMA20 — downtrend, bounce tạm
    # -2: close<SMA20<SMA50 — bear aligned hoàn toàn
    if "sma20" in df.columns and "sma50" in df.columns:
        s20, s50 = df["sma20"], df["sma50"]
        sma_diff_pct = (s20 - s50) / s50.replace(0, np.nan) * 100
        bull_structure = s20 > s50                    # SMA20 > SMA50
        price_above_s20 = close > s20

        ma_score = pd.Series(0, index=df.index, dtype=int)
        ma_score = ma_score.where(sma_diff_pct.abs() > 1, 0)  # sideways → 0
        ma_score = ma_score.where(~(bull_structure & price_above_s20), ma_score)
        ma_score[bull_structure & price_above_s20]   = 2
        ma_score[bull_structure & ~price_above_s20]  = 1
        ma_score[~bull_structure & price_above_s20]  = -1
        ma_score[~bull_structure & ~price_above_s20] = -2
        # Khi SMA20 ≈ SMA50 (sideways) → ghi đè về 0
        ma_score[sma_diff_pct.abs() <= 1] = 0
        df["ma_aligned"] = ma_score
    else:
        df["ma_aligned"] = 0

    # Price trend 20d: % thay đổi close so với 20 phiên trước
    df["price_trend_20d"] = close.pct_change(20) * 100

    # RS 14d vs VNI — tính trong add_all_indicators nếu có cột vni_ret_14d được inject từ ngoài
    # (screener/backtester inject cột này trước khi gọi add_all_indicators)
    if "vni_ret_14d" in df.columns:
        stock_ret_14d  = close.pct_change(14) * 100
        df["rs_14d"] = stock_ret_14d - df["vni_ret_14d"]
    else:
        df["rs_14d"] = np.nan

    # Multi-timeframe: weekly MACD trend (Elder Triple Screen)
    df["weekly_macd_trend"] = _calc_weekly_macd_trend(df)

    return df


def _calc_weekly_macd_trend(df: pd.DataFrame) -> pd.Series:
    """Resample daily → weekly, tính MACD histogram, map ngược về daily.

    Returns Series index=df.index, values: +1 (weekly uptrend) / -1 (downtrend) / 0 (unknown).
    Elder Triple Screen: chỉ mua khi weekly MACD histogram > 0.
    """
    if "Date" not in df.columns or len(df) < 40:
        return pd.Series(0, index=df.index, dtype=int)
    try:
        tmp = df.set_index("Date")[["close"]].copy()
        weekly_close = tmp["close"].resample("W").last().dropna()
        if len(weekly_close) < 35:
            return pd.Series(0, index=df.index, dtype=int)
        _, _, w_hist = calc_macd(weekly_close)
        # Map weekly signal về daily index bằng forward-fill (mỗi tuần đổi tín hiệu 1 lần)
        w_daily = w_hist.reindex(tmp.index, method="ffill").fillna(0)
        result = pd.Series(0, index=df.index, dtype=int)
        # Align theo positional index (df có thể đã reset_index)
        result.values[:] = np.where(w_daily.values > 0, 1, np.where(w_daily.values < 0, -1, 0))
        return result
    except Exception:
        return pd.Series(0, index=df.index, dtype=int)


# ── Phân loại tín hiệu ───────────────────────────────────────────────────────

def calculate_tech_score(
    rsi: float,
    macd_hist: float,
    dist_ema_pct: float,
    ma_aligned: int = 0,
    volume_ratio: float = float("nan"),
    macd_bars_since_cross: int = 999,
    phase: str = "Neutral",          # Wyckoff phase → bonus/penalty
    weekly_macd_trend: int = 0,      # Elder Triple Screen: +1/-1/0
    rs_pct: float = float("nan"),    # Relative Strength vs VNI 14d (O'Neil CANSLIM)
) -> float:
    """Tính tech_score 0–100 từ 7 chiều độc lập (rebalanced v2 cho VN market).

    Chiều 1 — Momentum/overbought (RSI Wilder): ±15 điểm
    Chiều 2 — MACD freshness-adjusted: ±15/10/5 tùy cross mới/cũ
    Chiều 3 — Trend structure (MA alignment): ±16 điểm  ← tăng từ ±13 (dự báo tốt nhất VN)
    Chiều 4 — Mean reversion position (Dist EMA34): ±8 điểm  ← giảm từ ±10
    Bonus 1  — Volume confirmation (Granville): ±5 điểm
    Bonus 2  — Wyckoff Phase: Accumulation/Markup +6/+4, Distribution/Markdown -8/-6
    Bonus 3  — Elder Weekly MACD (Triple Screen): ±8 điểm  ← tăng từ ±6
    Bonus 4  — Relative Strength vs VNI 14d (O'Neil): ±8 điểm  ← tăng từ ±6
    """
    score = 50.0

    # ── Chiều 1: RSI — momentum/overbought position (±15) ────────────────────
    if rsi <= RSI_OVERSOLD:        # oversold → cơ hội đảo chiều
        score += 15
    elif rsi >= RSI_OVERBOUGHT:    # overbought → áp lực chốt
        score -= 15
    else:
        # Tuyến tính trong vùng 30–70; trung tính tại RSI=50
        score += (50 - rsi) * 15 / 20

    # ── Chiều 2: MACD freshness-adjusted (±15/10/5) ──────────────────────────
    # Cross mới (1-3 bars) = signal khởi đầu mạnh nhất
    # Cross cũ (>10 bars)  = momentum đã tích hợp nhiều, predictive power giảm
    if macd_hist > 0:
        if macd_bars_since_cross <= 3:    score += 15   # fresh cross
        elif macd_bars_since_cross <= 10: score += 10   # còn tươi
        else:                             score += 5    # cũ — giảm weight
    elif macd_hist < 0:
        if macd_bars_since_cross <= 3:    score -= 15
        elif macd_bars_since_cross <= 10: score -= 10
        else:                             score -= 5

    # ── Chiều 3: MA Alignment — trend structure (±16) ─────────────────────────
    # Tăng từ ±13 → ±16: VN backtest cho thấy MA structure là yếu tố dự báo mạnh nhất
    # cho medium-term return; stocks in bull structure consistently outperform
    #  +2: close>SMA20>SMA50 (bull aligned, uptrend rõ)
    #  +1: SMA20>SMA50 nhưng close<SMA20 (uptrend nhưng pullback)
    #   0: SMA20 ≈ SMA50 (sideways)
    #  -1: SMA20<SMA50 nhưng close>SMA20 (downtrend, bounce nhỏ)
    #  -2: close<SMA20<SMA50 (bear aligned, downtrend rõ)
    score += ma_aligned * 8.0   # ±2 × 8.0 = ±16

    # ── Chiều 4: Dist EMA34 — mean reversion position (±8) ───────────────────
    # Giảm từ ±10 → ±8: pullback EMA34 ít dự báo hơn MA structure trong VN
    if -15 <= dist_ema_pct < -5:
        score += 8    # pullback lý tưởng
    elif -5 <= dist_ema_pct <= 5:
        score += 2    # sát EMA34, trung tính
    elif dist_ema_pct < -15:
        score -= 8    # downtrend sâu
    elif dist_ema_pct > 15:
        score -= 8    # overextended cao
    elif dist_ema_pct > 5:
        score -= 2    # xa EMA34 nhẹ

    # ── Bonus 1: Volume confirmation (±5) ─────────────────────────────────────
    if not math.isnan(volume_ratio):
        if volume_ratio >= 1.5:
            score += 5
        elif volume_ratio < 0.6:
            score -= 5

    # ── Bonus 2: Wyckoff Phase (±6/±8) ────────────────────────────────────────
    _PHASE_BONUS = {
        "Accumulation": 6,
        "Markup":        4,
        "Neutral":       0,
        "Distribution": -8,
        "Markdown":     -6,
    }
    score += _PHASE_BONUS.get(phase, 0)

    # ── Bonus 3: Elder Triple Screen — Weekly MACD (±8) ───────────────────────
    # Tăng từ ±6 → ±8: weekly trend alignment là bộ lọc quan trọng trong VN
    score += weekly_macd_trend * 8

    # ── Bonus 4: Relative Strength vs VNI 14d (O'Neil CANSLIM) (±8) ──────────
    # Tăng từ ±6 → ±8: RS outperformance trong VN là leading indicator đáng tin
    if not math.isnan(rs_pct):
        if rs_pct > 5:     score += 8    # outperform mạnh
        elif rs_pct > 0:   score += 4    # outperform nhẹ
        elif rs_pct > -5:  score -= 4    # underperform nhẹ
        else:              score -= 8    # underperform mạnh

    return max(0.0, min(100.0, score))


def classify_signal(
    tech_score: float,
    volume_ratio: float = float("nan"),
    rsi: float = float("nan"),
) -> str:
    """Volume Gate VN-adjusted + RSI Gate cho SELL:
    - BUY-A bị hạ khi volume_ratio > 4.0 (FOMO cực đoan); 2-4x là breakout thật trong VN
    - Không hạ khi volume thấp — tích lũy lặng lẽ trong VN là dấu hiệu tốt
    - SELL-A bị hạ khi volume_ratio < 0.5 (thiếu lực bán — SELL tín hiệu yếu)
    - SELL-A bị hạ khi RSI < 40 — cổ phiếu gần oversold ở VN thường bounce mạnh,
      không nên SELL-A (risk bắt đáy sai chiều); chỉ SELL-A khi RSI ≥ 40 (phân phối thật)
    """
    if tech_score >= SCORE_BUY_A:
        if not math.isnan(volume_ratio) and volume_ratio > 4.0:
            return "BUY-B"   # volume >4x trong VN = FOMO retail cuối đợt; 2-4x vẫn là breakout
        return "BUY-A"
    elif tech_score >= SCORE_BUY_B:
        return "BUY-B"
    elif tech_score >= SCORE_SELL_B:
        return "HOLD"
    elif tech_score >= SCORE_SELL_A:
        if not math.isnan(volume_ratio) and volume_ratio < 0.5:
            return "HOLD"    # SELL signal thiếu volume = chưa có lực bán thực sự
        return "SELL-B"
    else:
        # SELL-A: chỉ khi RSI không quá thấp (tránh nhầm oversold bounce với downtrend thật)
        if not math.isnan(rsi) and rsi < 40:
            return "SELL-B"  # RSI gần oversold → VN market bounce → không SELL-A
        return "SELL-A"


def classify_risk(
    tech_score: float,
    dist_ema_pct: float,
    atr_pct: float = float("nan"),
    bb_width_pct: float = float("nan"),
    volume_ratio: float = float("nan"),
) -> str:
    """Phân loại rủi ro đa chiều:
    - Momentum: tech_score + dist_ema_pct  (Elder Triple Screen)
    - Volatility: atr_pct (Wilder ATR), bb_width_pct (Bollinger)
    - Confirmation: volume_ratio (Granville Volume Law)
    Mỗi chiều tính điểm phạt; tổng điểm phạt quyết định mức rủi ro.
    """
    penalty = 0

    # ── Momentum (Elder) ─────────────────────────────────────────────────────
    if tech_score < SCORE_SELL_B:
        penalty += 2    # xu hướng yếu/xuống
    elif tech_score < SCORE_BUY_B:
        penalty += 1    # trung tính
    if dist_ema_pct > 12:
        penalty += 2    # quá xa EMA — rubber-band căng
    elif dist_ema_pct > 6:
        penalty += 1

    # ── Volatility: ATR (Wilder 1978) ────────────────────────────────────────
    # ATR/Giá > 3%: volatility cao → giá dao động mạnh, khó stop-loss chính xác
    if not math.isnan(atr_pct):
        if atr_pct > 4.5:
            penalty += 2
        elif atr_pct > 3.0:
            penalty += 1

    # ── Volatility: Bollinger Band Width (Bollinger 1983) ────────────────────
    # BB Width > 15%: đang trong giai đoạn expansion — rủi ro đảo chiều
    # BB Width < 5%: squeeze — sắp có biến động lớn, hướng chưa xác định
    if not math.isnan(bb_width_pct):
        if bb_width_pct > 15:
            penalty += 1   # đang giãn mạnh
        elif bb_width_pct < 5:
            penalty += 1   # squeeze — uncertainty cao

    # ── Volume Confirmation (Granville's Law) ────────────────────────────────
    # Tín hiệu mua nhưng volume thấp → thiếu xác nhận → tăng rủi ro
    if not math.isnan(volume_ratio):
        if tech_score >= SCORE_BUY_B and volume_ratio < 0.7:
            penalty += 1   # BUY signal nhưng volume cạn

    # ── Kết luận ─────────────────────────────────────────────────────────────
    if penalty <= 1:
        return "Low"
    elif penalty <= 3:
        return "Medium"
    else:
        return "High"


def classify_phase(
    rsi: float,
    dist_ema_pct: float,
    price_trend_20d: float = 0.0,   # % thay đổi giá 20 phiên gần nhất
    ma_aligned: int = 0,            # -2/-1/0/+1/+2 từ MA alignment
) -> str:
    """Phân loại Wyckoff phase dựa trên 4 chiều.

    Wyckoff gốc đo hành vi giá-volume theo thời gian. Ở đây dùng proxy:
    - RSI: đo momentum hiện tại
    - dist_ema_pct: đo vị trí giá so với xu hướng trung hạn
    - price_trend_20d: đo hướng di chuyển thực tế 20 phiên (thay thế HH/HL pattern)
    - ma_aligned: đo cấu trúc xu hướng dài hạn (SMA20 vs SMA50)
    """
    # Markdown: giá đang giảm rõ ràng, cả momentum lẫn cấu trúc đều yếu
    if rsi < 45 and dist_ema_pct < -8 and (price_trend_20d < -5 or ma_aligned <= -1):
        return "Markdown"

    # Distribution: giá cao, momentum hạ nhiệt, xu hướng bắt đầu yếu
    if rsi > 58 and dist_ema_pct > 5 and price_trend_20d < 2 and ma_aligned >= 0:
        return "Distribution"

    # Markup: giá đang tăng rõ ràng, cấu trúc MA ủng hộ
    if rsi > 52 and dist_ema_pct > 0 and (price_trend_20d > 3 or ma_aligned >= 1):
        return "Markup"

    # Accumulation: giá sideways sau downtrend, momentum chưa hồi phục
    # Điều kiện: không giảm tiếp (dist không quá thấp), nhưng chưa có trend tăng rõ
    if rsi < 55 and -20 <= dist_ema_pct < 5 and -3 <= price_trend_20d < 5:
        return "Accumulation"

    return "Neutral"


# ── Nhận diện mẫu hình ──────────────────────────────────────────────────────
#
# Số liệu tỷ lệ thành công (breakout rate) từ Thomas Bulkowski,
# "Encyclopedia of Chart Patterns" (2nd ed.) + thepatternsite.com
# Ghi chú: tỷ lệ đo trên thị trường Mỹ; VN có thể lệch ±5-10%
#
# Mỗi mẫu trả tuple: (tên_hiển_thị, hướng, tỷ_lệ_thành_công_%)
# hướng: "bull" | "bear" | "neutral"

# ── Mẫu nến (Candlestick) ────────────────────────────────────────────────────

def _detect_timeframe(df: pd.DataFrame) -> str:
    """Phát hiện timeframe dựa vào khoảng cách trung bình giữa các phiên.
    Trả: 'daily' | 'weekly' | 'monthly' | 'unknown'
    Dùng cột 'time' hoặc index nếu có dạng date.
    """
    try:
        time_col = None
        if "time" in df.columns:
            time_col = pd.to_datetime(df["time"], errors="coerce").dropna()
        elif hasattr(df.index, "dtype") and str(df.index.dtype).startswith("datetime"):
            time_col = pd.Series(df.index)
        if time_col is None or len(time_col) < 5:
            return "unknown"
        gap = time_col.diff().dt.days.dropna()
        med = gap.median()
        if med <= 2:
            return "daily"
        elif med <= 9:
            return "weekly"
        elif med <= 35:
            return "monthly"
        return "unknown"
    except Exception:
        return "unknown"


def detect_candle_patterns(df: pd.DataFrame) -> tuple[list[tuple[str, str, int]], str]:
    """Nhận diện mẫu nến Nhật tại 3 phiên cuối.

    Trả (patterns, timeframe):
      patterns  : list[tuple[tên, hướng, confidence%]]
      timeframe : 'daily' | 'weekly' | 'none'

    Quy tắc theo timeframe:
      daily   — Bulkowski stats nguyên bản (đúng context đo gốc)
      weekly  — detect được nhưng confidence giảm 10% + suffix [W] vào tên
                (weekly VN ~ daily Mỹ về tín hiệu, nhưng thiếu bằng chứng thống kê)
      monthly / unknown — trả ([], 'none') vì quá ít điểm dữ liệu
    """
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(df.columns) or len(df) < 3:
        return [], "none"

    tf = _detect_timeframe(df)
    if tf == "monthly":
        return [], "none"

    df = df.dropna(subset=list(needed))
    if len(df) < 3:
        return [], "none"

    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    i = len(df) - 1

    patterns: list[tuple[str, str, int]] = []

    body       = abs(c[i] - o[i])
    full_rng   = h[i] - l[i]
    upper_wick = h[i] - max(c[i], o[i])
    lower_wick = min(c[i], o[i]) - l[i]
    bull = c[i] > o[i]
    if full_rng == 0:
        return [], "none"

    # Ngưỡng body tối thiểu: so sánh với trung bình body 10 phiên gần nhất
    # Candle quá nhỏ (< 30% avg body) không phản ánh gì — bỏ qua.
    _n = min(10, i)
    avg_body = float(np.mean(np.abs(c[i-_n:i] - o[i-_n:i]))) if _n > 0 else body
    min_body = avg_body * 0.3   # ngưỡng "có ý nghĩa"

    # Doji — body gần bằng 0 so với full range, nhưng full_rng phải đủ lớn
    if body / full_rng < 0.10 and full_rng >= avg_body * 0.5:
        patterns.append(("Doji (thập giá)", "neutral", 53))

    # Hammer / Hanging Man
    if body >= min_body and lower_wick >= 2 * body and upper_wick < body * 1.5:
        # Trend context 10 bar: downtrend rõ → Hammer; uptrend → Hanging Man
        _w = min(10, i)
        prev_down = i >= _w and c[i-1] < c[i-_w] * 0.97   # giảm ≥3% trong 10 bar
        prev_up   = i >= _w and c[i-1] > c[i-_w] * 1.03
        if prev_down:
            patterns.append(("Hammer (bua)", "bull", 60))
        elif prev_up:
            patterns.append(("Hanging Man (nguoi treo)", "bear", 59))

    # Shooting Star / Inverted Hammer
    if body >= min_body and upper_wick >= 2 * body and lower_wick < body * 1.5:
        _w = min(10, i)
        prev_up   = i >= _w and c[i-1] > c[i-_w] * 1.03
        prev_down = i >= _w and c[i-1] < c[i-_w] * 0.97
        if prev_up:
            patterns.append(("Shooting Star (sao bang)", "bear", 59))
        elif prev_down:
            patterns.append(("Inverted Hammer (can xac nhan)", "neutral", 50))

    # Marubozu — body phải ≥ avg_body để không trigger trên candle tí hon
    if body >= avg_body * 0.8 and body / full_rng >= 0.90:
        if bull:
            patterns.append(("Marubozu tang (luc mua ap dao)", "bull", 68))
        else:
            patterns.append(("Marubozu giam (luc ban ap dao)", "bear", 68))

    # Bullish Engulfing
    if i >= 1:
        prev_body = abs(c[i-1] - o[i-1])
        if (bull and c[i-1] < o[i-1]
                and o[i] <= c[i-1] and c[i] >= o[i-1]
                and body >= avg_body * 0.5 and prev_body >= avg_body * 0.5):
            patterns.append(("Bullish Engulfing (nuot nen giam)", "bull", 63))

    # Bearish Engulfing
    if i >= 1:
        prev_body = abs(c[i-1] - o[i-1])
        if (not bull and c[i-1] > o[i-1]
                and o[i] >= c[i-1] and c[i] <= o[i-1]
                and body >= avg_body * 0.5 and prev_body >= avg_body * 0.5):
            patterns.append(("Bearish Engulfing (nuot nen tang)", "bear", 63))

    # Morning Star — nến 2 phải mở thấp hơn close nến 1 (gap down hoặc sát đáy thân)
    # nến 3 đóng trên midpoint thân nến 1; wick nến 3 không quá dài
    if i >= 2:
        b1 = abs(c[i-2] - o[i-2])
        b3 = abs(c[i]   - o[i])
        rng1 = h[i-2] - l[i-2]
        rng3 = h[i]   - l[i]
        n1_bear  = c[i-2] < o[i-2] and b1 > rng1 * 0.5 and b1 >= avg_body * 0.5
        # nến 2: mở trong/dưới phần thấp của thân nến 1, body nhỏ
        n2_small = (abs(c[i-1]-o[i-1]) < (h[i-1]-l[i-1]) * 0.35
                    and max(c[i-1], o[i-1]) <= c[i-2] + b1 * 0.3)
        n3_bull  = bull and b3 > rng3 * 0.5 and b3 >= avg_body * 0.5
        # nến 3 đóng trên midpoint thân nến 1; upper wick không quá 50% body (lực mua thật)
        n3_clean = upper_wick < b3 * 0.5
        if n1_bear and n2_small and n3_bull and n3_clean and c[i] > (o[i-2] + c[i-2]) / 2:
            patterns.append(("Morning Star (dao chieu tang manh)", "bull", 78))

    # Evening Star — nến 2 mở cao hơn close nến 1, body nhỏ; nến 3 đóng dưới midpoint nến 1
    if i >= 2:
        b1 = abs(c[i-2] - o[i-2])
        b3 = abs(c[i]   - o[i])
        rng1 = h[i-2] - l[i-2]
        rng3 = h[i]   - l[i]
        n1_bull  = c[i-2] > o[i-2] and b1 > rng1 * 0.5 and b1 >= avg_body * 0.5
        n2_small = (abs(c[i-1]-o[i-1]) < (h[i-1]-l[i-1]) * 0.35
                    and min(c[i-1], o[i-1]) >= c[i-2] - b1 * 0.3)
        n3_bear  = not bull and b3 > rng3 * 0.5 and b3 >= avg_body * 0.5
        n3_clean = lower_wick < b3 * 0.5   # lower wick ngắn — áp lực bán thật
        if n1_bull and n2_small and n3_bear and n3_clean and c[i] < (o[i-2] + c[i-2]) / 2:
            patterns.append(("Evening Star (dao chieu giam)", "bear", 72))

    # Three White Soldiers — body >= avg*0.5, mỗi nến mở trong thân nến trước
    # THÊM: upper wick phải ngắn (< 20% body) — soldiers thật không có bóng trên dài
    if i >= 2:
        def _uw(j): return h[j] - max(c[j], o[j])
        soldiers_ok = (
            all(c[j] > o[j] and c[j] > c[j-1]
                and abs(c[j]-o[j]) >= avg_body * 0.5
                and _uw(j) < abs(c[j]-o[j]) * 0.20    # upper wick < 20% body
                for j in [i-2, i-1, i])
            and o[i-1] >= o[i-2] and o[i-1] <= c[i-2]
            and o[i]   >= o[i-1] and o[i]   <= c[i-1]
        )
        if soldiers_ok:
            patterns.append(("Three White Soldiers (3 nen tang lien tiep)", "bull", 83))

    # Three Black Crows — body >= avg*0.5, lower wick ngắn (< 20% body)
    if i >= 2:
        def _lw(j): return min(c[j], o[j]) - l[j]
        crows_ok = (
            all(c[j] < o[j] and c[j] < c[j-1]
                and abs(c[j]-o[j]) >= avg_body * 0.5
                and _lw(j) < abs(c[j]-o[j]) * 0.20    # lower wick < 20% body
                for j in [i-2, i-1, i])
            and o[i-1] >= c[i-2] and o[i-1] <= o[i-2]
            and o[i]   >= c[i-1] and o[i]   <= o[i-1]
        )
        if crows_ok:
            patterns.append(("Three Black Crows (3 nen giam lien tiep)", "bear", 78))

    # Weekly: giảm confidence 10% + gắn [W] vào tên để phân biệt với daily
    if tf == "weekly":
        patterns = [(f"{n} [W]", d, max(50, r - 10)) for n, d, r in patterns]

    return patterns, tf if patterns else "none"


# ── Mẫu hình giá (Chart Patterns) ───────────────────────────────────────────

def detect_chart_patterns(df: pd.DataFrame) -> list[tuple[str, str, int]]:
    """Nhận diện mẫu hình giá trên 20–60 phiên cuối.
    Trả list tuple (tên, hướng, tỷ_lệ_%) theo Bulkowski.
    Yêu cầu ≥ 30 phiên dữ liệu.
    """
    needed = {"high", "low", "close"}
    if not needed.issubset(df.columns) or len(df) < 30:
        return []
    df = df.dropna(subset=list(needed)).tail(60).reset_index(drop=True)
    if len(df) < 30:
        return []

    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(c)
    patterns: list[tuple[str, str, int]] = []

    # ── Helper: tìm đỉnh/đáy cục bộ ─────────────────────────────────────────
    def local_highs(window: int = 5) -> list[int]:
        return [i for i in range(window, n - window)
                if h[i] == max(h[i-window:i+window+1])]

    def local_lows(window: int = 5) -> list[int]:
        return [i for i in range(window, n - window)
                if l[i] == min(l[i-window:i+window+1])]

    highs = local_highs()
    lows  = local_lows()
    tol   = 0.03   # 3% tolerance cho đỉnh/đáy tương đương

    # ── Helper: phân loại Adam (nhọn) vs Eve (tròn) ─────────────────────────
    # Adam: đáy/đỉnh hình thành trong 1-3 bar, giá biến động mạnh (V-shape)
    # Eve: đáy/đỉnh trải rộng nhiều bar, giá đi từ từ (U-shape)
    # Dùng tỷ lệ (high-low) của 3 bar quanh pivot so với ATR trung bình:
    # Adam: spread hẹp (1-2 bar chiếm phần lớn range) → std thấp
    # Eve: spread rộng (nhiều bar chia đều range) → std cao
    def _is_adam(idx: int, is_low: bool, half_w: int = 3) -> bool:
        start = max(0, idx - half_w)
        end   = min(n, idx + half_w + 1)
        if end - start < 3:
            return True
        segment = l[start:end] if is_low else h[start:end]
        span    = max(segment) - min(segment)
        if span == 0:
            return True
        # Adam: pivot chiếm >60% range trong vòng 1 bar (nhọn)
        neighbors = [abs(segment[j] - segment[j-1]) for j in range(1, len(segment))]
        max_step  = max(neighbors) if neighbors else 0
        # Nếu bước nhảy lớn nhất > 40% tổng span → đáy/đỉnh nhọn = Adam
        return max_step / span > 0.40

    # ── Double Bottom — thống kê VN (Bulkowski VN Edition 1, 2026) ───────────
    # Adam&Adam: 181 mẫu, 77.35% | Adam&Eve: 51 mẫu, 64.71%
    # Eve&Adam:  39 mẫu, 79.49%  | Eve&Eve:  31 mẫu, 74.19%
    if len(lows) >= 2:
        b1, b2 = lows[-2], lows[-1]
        if b2 > b1 and abs(l[b1] - l[b2]) / l[b1] < tol:
            mid_highs = [i for i in highs if b1 < i < b2]
            if mid_highs:
                neck = max(h[i] for i in mid_highs)
                # Khoảng cách đủ lớn (sách: đỉnh giữa ≥ 10% trên đáy thấp nhất)
                low_pt  = min(l[b1], l[b2])
                height  = neck - low_pt
                if height / low_pt >= 0.08 and c[-1] > neck * 0.995:
                    b1_adam = _is_adam(b1, is_low=True)
                    b2_adam = _is_adam(b2, is_low=True)
                    if b1_adam and b2_adam:
                        patterns.append(("Hai day Adam & Adam (VN: 77%)", "bull", 77))
                    elif b1_adam and not b2_adam:
                        patterns.append(("Hai day Adam & Eve (VN: 65%)", "bull", 65))
                    elif not b1_adam and b2_adam:
                        patterns.append(("Hai day Eve & Adam (VN: 79%)", "bull", 79))
                    else:
                        patterns.append(("Hai day Eve & Eve (VN: 74%)", "bull", 74))

    # ── Double Top — thống kê VN (Bulkowski VN Edition 1, 2026) ─────────────
    # Adam&Adam: 141 mẫu, 72.34% | Adam&Eve: 45 mẫu, 77.78%
    # Eve&Adam:  34 mẫu, 73.53%  | Eve&Eve:  1 mẫu (bỏ qua — không đủ mẫu)
    if len(highs) >= 2:
        t1, t2 = highs[-2], highs[-1]
        if t2 > t1 and abs(h[t1] - h[t2]) / h[t1] < tol:
            mid_lows = [i for i in lows if t1 < i < t2]
            if mid_lows:
                neck = min(l[i] for i in mid_lows)
                high_pt = max(h[t1], h[t2])
                height  = high_pt - neck
                if height / neck >= 0.08 and c[-1] < neck * 1.005:
                    t1_adam = _is_adam(t1, is_low=False)
                    t2_adam = _is_adam(t2, is_low=False)
                    if t1_adam and t2_adam:
                        patterns.append(("Hai dinh Adam & Adam (VN: 72%)", "bear", 72))
                    elif t1_adam and not t2_adam:
                        patterns.append(("Hai dinh Adam & Eve (VN: 78%)", "bear", 78))
                    elif not t1_adam and t2_adam:
                        patterns.append(("Hai dinh Eve & Adam (VN: 74%)", "bear", 74))
                    else:
                        patterns.append(("Hai dinh Eve & Eve (it mau)", "bear", 70))

    # ── Head & Shoulders đỉnh — VN: 72.73% (11 mẫu), phức hợp: 72.56% (164 mẫu)
    if len(highs) >= 3:
        ls, hd, rs = highs[-3], highs[-2], highs[-1]
        head_h = h[hd]
        if (h[ls] < head_h and h[rs] < head_h
                and abs(h[ls] - h[rs]) / head_h < tol * 2
                and rs - ls >= 20):
            mid_lows_l = [i for i in lows if ls < i < hd]
            mid_lows_r = [i for i in lows if hd < i < rs]
            if mid_lows_l and mid_lows_r:
                neck = (l[mid_lows_l[-1]] + l[mid_lows_r[0]]) / 2
                if c[-1] < neck * 1.03:
                    patterns.append(("Vai dau vai dinh (VN: 73%)", "bear", 73))

    # ── Inverse H&S đáy — VN: 81.25% (32 mẫu), phức hợp: 72.73% (99 mẫu) ───
    if len(lows) >= 3:
        ls, hd, rs = lows[-3], lows[-2], lows[-1]
        head_l = l[hd]
        if (l[ls] > head_l and l[rs] > head_l
                and abs(l[ls] - l[rs]) / max(l[ls], l[rs]) < tol * 2
                and rs - ls >= 20):
            mid_highs_l = [i for i in highs if ls < i < hd]
            mid_highs_r = [i for i in highs if hd < i < rs]
            if mid_highs_l and mid_highs_r:
                neck = (h[mid_highs_l[-1]] + h[mid_highs_r[0]]) / 2
                if c[-1] > neck * 0.97:
                    patterns.append(("Vai dau vai day (VN: 81%)", "bull", 81))

    # ── Ascending Triangle — Bulkowski: 77% bull breakout ────────────────────
    if len(highs) >= 3 and len(lows) >= 3:
        rec_highs = highs[-3:]
        rec_lows  = lows[-3:]
        flat_top = (max(h[i] for i in rec_highs) - min(h[i] for i in rec_highs)) \
                   / max(h[i] for i in rec_highs) < tol
        rising_lows = l[rec_lows[-1]] > l[rec_lows[0]] * 1.01
        if flat_top and rising_lows:
            patterns.append(("Ascending Triangle (tam giac tang)", "bull", 77))

    # ── Descending Triangle — Bulkowski: 72% bear breakout ───────────────────
    if len(highs) >= 3 and len(lows) >= 3:
        rec_highs = highs[-3:]
        rec_lows  = lows[-3:]
        flat_bot = (max(l[i] for i in rec_lows) - min(l[i] for i in rec_lows)) \
                   / max(l[i] for i in rec_lows) < tol
        falling_highs = h[rec_highs[-1]] < h[rec_highs[0]] * 0.99
        if flat_bot and falling_highs:
            patterns.append(("Descending Triangle (tam giac giam)", "bear", 72))

    # ── Symmetrical Triangle — Bulkowski: 54% bull / 46% bear (uncertain) ────
    if len(highs) >= 3 and len(lows) >= 3:
        rec_highs = highs[-3:]
        rec_lows  = lows[-3:]
        falling_h = h[rec_highs[-1]] < h[rec_highs[0]] * 0.99
        rising_l  = l[rec_lows[-1]]  > l[rec_lows[0]]  * 1.01
        if falling_h and rising_l:
            patterns.append(("Symmetrical Triangle (tam giac can bang)", "neutral", 54))

    # ─────────────────── PHASE 2 PATTERNS ────────────────────────────────────

    # Volume array (optional — dùng cho Flag và Gap)
    vol = df["volume"].values if "volume" in df.columns else None

    # ── 1. Flag / Pennant — có volume confirmation ────────────────────────────
    # Pole: volume lớn (tăng mạnh có lực); consolidation: volume co lại (giảm quan tâm)
    if n >= 25 and vol is not None:
        pole_bars = 12
        cons_bars = 6
        if n >= pole_bars + cons_bars:
            pole_start = n - pole_bars - cons_bars
            pole_end   = n - cons_bars

            c_pole  = c[pole_start:pole_end]
            c_cons  = c[pole_end:]
            h_cons  = h[pole_end:]
            l_cons  = l[pole_end:]
            v_pole  = vol[pole_start:pole_end]
            v_cons  = vol[pole_end:]

            pole_move  = (c_pole[-1] - c_pole[0]) / c_pole[0] if c_pole[0] > 0 else 0
            cons_range = (h_cons.max() - l_cons.min()) / c_pole[-1] if c_pole[-1] > 0 else 0

            # Volume confirmation: pole avg > consolidation avg (volume co lại khi nghỉ)
            v_pole_avg = v_pole.mean()
            v_cons_avg = v_cons.mean()
            vol_contraction = v_cons_avg < v_pole_avg * 0.80 if v_pole_avg > 0 else False

            if vol_contraction:
                if pole_move >= 0.90 and cons_range <= 0.25:
                    # High Tight Flag VN 63%
                    patterns.append(("High Tight Flag (co cao va chat)", "bull", 63))

                elif pole_move >= 0.15 and cons_range <= 0.25:
                    # Bull flag/pennant: consolidation phải nghiêng ngược chiều pole (xuống)
                    cons_slope = (c_cons[-1] - c_cons[0]) / c_cons[0] if c_cons[0] > 0 else 0
                    if -0.12 <= cons_slope <= 0.02:
                        if len(h_cons) >= 4:
                            mid = len(h_cons) // 2
                            # Pennant: cả highs lẫn lows hội tụ vào nhau
                            h_shrinks = h_cons[:mid].max() > h_cons[mid:].max() * 1.005
                            l_rises   = l_cons[:mid].min() < l_cons[mid:].min() * 0.995
                            if h_shrinks and l_rises:
                                patterns.append(("Bull Pennant (co duoi nheo tang)", "bull", 70))
                            else:
                                patterns.append(("Bull Flag (co tang)", "bull", 68))
                        else:
                            patterns.append(("Bull Flag (co tang)", "bull", 68))

                elif pole_move <= -0.15 and cons_range <= 0.25:
                    # Bear flag/pennant: consolidation nghiêng lên (counter-trend nhẹ)
                    cons_slope = (c_cons[-1] - c_cons[0]) / c_cons[0] if c_cons[0] > 0 else 0
                    if -0.02 <= cons_slope <= 0.12:
                        if len(h_cons) >= 4:
                            mid = len(h_cons) // 2
                            h_shrinks = h_cons[:mid].max() > h_cons[mid:].max() * 1.005
                            l_rises   = l_cons[:mid].min() < l_cons[mid:].min() * 0.995
                            if h_shrinks and l_rises:
                                patterns.append(("Bear Pennant (co duoi nheo giam)", "bear", 54))
                            else:
                                patterns.append(("Bear Flag (co giam)", "bear", 70))
                        else:
                            patterns.append(("Bear Flag (co giam)", "bear", 70))

    # ── 2. Gap patterns — chỉ 5 bars gần nhất, volume ≥ 1.5x SMA20 ──────────
    if "open" in df.columns and vol is not None and n >= 21:
        o       = df["open"].values
        _slices = [vol[max(0, i-20):i] for i in range(n)]
        vol_ma  = np.array([s.mean() if len(s) > 0 else np.nan for s in _slices])
        # Chỉ xét 5 bars gần nhất để tránh pickup gap cũ không còn relevant
        for i in range(max(1, n - 5), n):
            prev_c   = c[i - 1]
            if prev_c <= 0:
                continue
            curr_o   = o[i]
            gap_pct  = abs(curr_o - prev_c) / prev_c
            if gap_pct < 0.02:
                continue
            # Volume phải đột biến ≥ 1.5× SMA20 ngày gap
            vol_ref  = vol_ma[i]
            if vol_ref <= 0 or vol[i] < vol_ref * 1.5:
                continue

            is_up_gap   = curr_o > prev_c * 1.02
            is_down_gap = curr_o < prev_c * 0.98

            # Trend 10 bar trước gap (cần rõ hơn: ±10%)
            trend_win   = c[max(0, i - 11):i - 1]
            if len(trend_win) < 5:
                continue
            prior_trend = (trend_win[-1] - trend_win[0]) / trend_win[0] if trend_win[0] > 0 else 0

            if is_up_gap and prior_trend < -0.10:
                # Gap Phá Nền tăng: gap lên sau downtrend → đảo chiều — VN 99.64%
                patterns.append(("Gap Pha Nen tang (breakaway gap)", "bull", 99))
                break
            elif is_down_gap and prior_trend > 0.10:
                # Gap Phá Nền giảm: gap xuống sau uptrend → đảo chiều — VN 99.64%
                patterns.append(("Gap Pha Nen giam (breakaway gap)", "bear", 99))
                break
            elif is_up_gap and prior_trend >= 0.10:
                # Gap Tiếp Diễn tăng: gap lên trong uptrend → tiếp diễn — VN 99.89%
                patterns.append(("Gap Tiep Dien tang (runaway gap)", "bull", 99))
                break
            elif is_down_gap and prior_trend <= -0.10:
                # Gap Tiếp Diễn giảm: gap xuống trong downtrend → tiếp diễn — VN 99.89%
                patterns.append(("Gap Tiep Dien giam (runaway gap)", "bear", 99))
                break

    # ── 3. Pipe Bottoms — 2 spike đáy liền kề, cùng mức, hồi phục sau — VN 94.28% ──
    # Fix: spike1 không so sánh l1 vs l2 nữa (tránh mâu thuẫn với same_lvl)
    for i in range(max(2, n - 15), n - 2):
        l1, l2 = l[i], l[i + 1]
        if l1 <= 0 or l2 <= 0:
            continue
        # Mỗi bar phải thấp hơn bar trước và bar sau (spike xuống rõ)
        spike1 = l1 < l[i - 1] * 0.97 and l1 < l[i + 2] * 0.97   # so vs bar i+2, không so vs l2
        spike2 = l2 < l[i - 1] * 0.97 and (i + 2 >= n or l2 < l[i + 2] * 0.97)
        # 2 đáy phải ở cùng mức (trong 3%)
        same_lvl  = abs(l1 - l2) / max(l1, l2) <= 0.03
        # Cần phục hồi rõ ràng sau spike (close bar thứ 3 sau > đáy + 5%)
        recovered = i + 3 < n and c[i + 3] > max(l1, l2) * 1.05
        if spike1 and spike2 and same_lvl and recovered:
            patterns.append(("Pipe Bottoms (day ong)", "bull", 94))
            break

    # ── 4. Rectangle Bottom — VN 79.6% ───────────────────────────────────────
    if len(highs) >= 4 and len(lows) >= 4:
        rec_h = highs[-4:]
        rec_l = lows[-4:]
        h_vals = [h[i] for i in rec_h]
        l_vals = [l[i] for i in rec_l]
        h_flat = (max(h_vals) - min(h_vals)) / min(h_vals) <= 0.03 if min(h_vals) > 0 else False
        l_flat = (max(l_vals) - min(l_vals)) / min(l_vals) <= 0.03 if min(l_vals) > 0 else False
        band_h = (min(h_vals) - max(l_vals)) / max(l_vals) if max(l_vals) > 0 else 0
        pre_c  = c[max(0, rec_l[0] - 20):rec_l[0]]
        prior_down = (pre_c[-1] - pre_c[0]) / pre_c[0] < -0.10 if len(pre_c) >= 5 and pre_c[0] > 0 else False
        if h_flat and l_flat and band_h >= 0.03 and prior_down:
            patterns.append(("Rectangle Bottom (day chu nhat)", "bull", 79))

    # ── 5. Triple patterns — dùng 3 đỉnh/đáy mới nhất + neckline check ───────
    if len(highs) >= 3:
        top3 = highs[-3:]   # 3 đỉnh cục bộ gần nhất (không bỏ qua 2 đỉnh cuối)
        t_vals = [h[i] for i in top3]
        t_max, t_min = max(t_vals), min(t_vals)

        # Neckline = mức thấp nhất giữa 3 đỉnh
        between_lows = [l[j] for j in range(top3[0], top3[-1] + 1)]
        neckline = min(between_lows) if between_lows else None

        # Ba Đỉnh Ngang Vùng: 3 highs cùng mức (≤3%), giá hiện tại đã phá neckline — VN 70.91%
        if t_min > 0 and (t_max - t_min) / t_min <= 0.03:
            if neckline and c[-1] < neckline * 0.99:
                patterns.append(("Triple Top flat (ba dinh ngang)", "bear", 70))
        # Ba Đỉnh Thấp Dần: mỗi đỉnh thấp hơn đỉnh trước ≥1%, giá phá neckline — VN 47.72%
        elif h[top3[0]] > h[top3[1]] * 1.01 and h[top3[1]] > h[top3[2]] * 1.01:
            if neckline and c[-1] < neckline * 0.99:
                patterns.append(("Triple Top Descending (ba dinh thap dan)", "bear", 47))

    if len(lows) >= 3:
        bot3 = lows[-3:]    # 3 đáy cục bộ gần nhất
        b_vals = [l[i] for i in bot3]

        # Neckline = mức cao nhất giữa 3 đáy
        between_highs = [h[j] for j in range(bot3[0], bot3[-1] + 1)]
        neckline_b = max(between_highs) if between_highs else None

        # Ba Đáy Cao Dần: mỗi đáy cao hơn đáy trước ≥1%, giá phá neckline lên — VN 71.93%
        if b_vals[0] < b_vals[1] * 0.99 and b_vals[1] < b_vals[2] * 0.99:
            if neckline_b and c[-1] > neckline_b * 1.01:
                patterns.append(("Triple Bottom Ascending (ba day cao dan)", "bull", 71))

    # ── Rounding: bỏ — quá nhiễu trên daily, chỉ đáng tin trên weekly ─────────

    return patterns


def detect_reversals(df: pd.DataFrame) -> dict:
    """Phát hiện 6 loại tín hiệu đảo chiều xác suất cao.

    Cơ sở lý thuyết:
      1. RSI Divergence         — Wilder (1978)
      2. MACD Zero-Cross        — Appel (1979), yêu cầu xác nhận 1–2 phiên
      3. Bollinger Band Bounce  — Bollinger (1983), mean reversion tại band extremes
      4. Wyckoff Spring/Upthrust — Wyckoff (1931), volume + test support/resistance
      5. RSI Momentum Reversal  — Elder (1993), RSI thoát khỏi vùng cực đoan
      6. Volume Climax          — Granville (1963), cạn cung/cầu tại cực đoan

    Returns dict:
        reversal_type:     "bullish" | "bearish" | "none"
        reversal_strength: 0–95  (tỷ lệ thành công ước tính, không có certainty 100%)
        reversal_signals:  chuỗi pipe-separated mô tả chi tiết từng tín hiệu
    """
    _EMPTY = {"reversal_type": "none", "reversal_strength": 0, "reversal_signals": ""}
    if len(df) < 20:
        return _EMPTY

    df_c = df.dropna(subset=["close"]).copy()
    if len(df_c) < 20:
        return _EMPTY

    bull_sigs: list[tuple[str, int]] = []   # (tên, confidence%)
    bear_sigs: list[tuple[str, int]] = []

    # ── 1. RSI Divergence (Wilder 1978) ─────────────────────────────────────
    # Fix #1: window=8 (tối thiểu 8 phiên giữa 2 pivot) để loại micro-pivot nhiễu.
    # Divergence chỉ có ý nghĩa khi 2 đáy/đỉnh cách nhau đủ xa — Wilder khuyến nghị
    # ít nhất 8–10 phiên để phân biệt swing thật với noise.
    if "rsi" in df_c.columns:
        rsi_s = df_c["rsi"].dropna()
        if len(rsi_s) >= 30:   # cần đủ bars để có 2 pivot cách nhau ≥8 phiên
            rv = rsi_s.values
            cv = df_c.loc[rsi_s.index, "close"].values
            m  = len(rv)
            w  = 8   # Fix #1: tăng từ 4 lên 8 phiên

            def _pivots_low(arr, wnd):
                return [i for i in range(wnd, m - wnd)
                        if arr[i] == min(arr[i - wnd: i + wnd + 1])]

            def _pivots_high(arr, wnd):
                return [i for i in range(wnd, m - wnd)
                        if arr[i] == max(arr[i - wnd: i + wnd + 1])]

            # Bullish divergence
            # Fix: dung RSI tai dung vi tri price pivot (rv[pl1], rv[pl2])
            # thay vi tim RSI pivot gan nhat (co the lech 3-5 bar → so sanh sai thoi diem)
            p_lows = _pivots_low(cv, w)
            if len(p_lows) >= 2:
                pl1, pl2 = p_lows[-2], p_lows[-1]
                if pl2 - pl1 >= 8:
                    price_lower = cv[pl2] < cv[pl1] * 0.985
                    rsi_higher  = rv[pl2] > rv[pl1] + 3.0
                    still_weak  = rv[m - 1] < 55
                    if price_lower and rsi_higher and still_weak:
                        bull_sigs.append((
                            "RSI Bullish Divergence (gia thap hon nhung RSI cao hon — dong luc dao chieu)", 68
                        ))

            # Bearish divergence
            p_highs = _pivots_high(cv, w)
            if len(p_highs) >= 2:
                ph1, ph2 = p_highs[-2], p_highs[-1]
                if ph2 - ph1 >= 8:
                    price_higher = cv[ph2] > cv[ph1] * 1.015
                    rsi_lower    = rv[ph2] < rv[ph1] - 3.0
                    still_strong = rv[m - 1] > 45
                    if price_higher and rsi_lower and still_strong:
                        bear_sigs.append((
                            "RSI Bearish Divergence (gia cao hon nhung RSI thap hon — dong luc suy yeu)", 68
                        ))

    # ── 2. MACD Zero-Cross với confirmation (Appel 1979) ─────────────────────
    # Yêu cầu xác nhận ≥1 phiên để loại false cross do nhiễu
    if "macd_hist" in df_c.columns:
        mh = df_c["macd_hist"].dropna()
        if len(mh) >= 5:
            mv = mh.values
            if mv[-1] > 0 and mv[-2] <= 0 and mv[-3] < 0:
                bull_sigs.append((
                    "MACD Zero-Cross tang (histogram vua doi duong, fresh signal)", 65
                ))
            # Fix: mv[-4] <= 0 chi bat duoc cross dung 4 bar truoc
            # Doi sang any() trong 7 bar gan nhat de bat ca cross xay ra 4-7 bar truoc
            elif (mv[-1] > mv[-2] > mv[-3] > 0
                  and len(mv) >= 7 and np.any(mv[-7:-3] <= 0)):
                bull_sigs.append((
                    "MACD tang xac nhan (3 phien duong tang dan sau zero-cross)", 70
                ))

            if mv[-1] < 0 and mv[-2] >= 0 and mv[-3] > 0:
                bear_sigs.append((
                    "MACD Zero-Cross giam (histogram vua doi am, fresh signal)", 65
                ))
            elif (mv[-1] < mv[-2] < mv[-3] < 0
                  and len(mv) >= 7 and np.any(mv[-7:-3] >= 0)):
                bear_sigs.append((
                    "MACD giam xac nhan (3 phien am giam dan sau zero-cross)", 70
                ))

    # ── 3. Bollinger Band Bounce (Bollinger 1983) ─────────────────────────────
    bb_cols = {"bb_upper", "bb_lower", "bb_mid"}
    if bb_cols.issubset(df_c.columns) and "rsi" in df_c.columns:
        bb_df = df_c.dropna(subset=["bb_upper", "bb_lower", "bb_mid", "rsi"])
        if len(bb_df) >= 5:
            tail5 = bb_df.tail(5)
            c5  = tail5["close"].values
            lo5 = tail5["bb_lower"].values
            hi5 = tail5["bb_upper"].values
            r5  = tail5["rsi"].values

            touched_lower = any(c5[i] <= lo5[i] * 1.005 for i in [-3, -2, -1])
            closed_above  = c5[-1] > lo5[-1]
            if touched_lower and closed_above and r5[-1] < 45:
                bull_sigs.append((
                    f"BB Lower Bounce (cham lower band, RSI {r5[-1]:.0f} < 45 — mean reversion)", 62
                ))

            touched_upper = any(c5[i] >= hi5[i] * 0.995 for i in [-3, -2, -1])
            closed_below  = c5[-1] < hi5[-1]
            if touched_upper and closed_below and r5[-1] > 55:
                bear_sigs.append((
                    f"BB Upper Bounce (cham upper band, RSI {r5[-1]:.0f} > 55 — mean reversion)", 62
                ))

    # ── 4. Wyckoff Spring / Upthrust (Wyckoff 1931) ──────────────────────────
    # Fix #2: thêm điều kiện range-bound — Spring chỉ hợp lệ khi giá đang tích lũy
    # trong biên hẹp (≤20% range/min), không phải đang downtrend thẳng.
    # Fix #3 (volume): block chỉ chạy khi volume_ratio có dữ liệu (dropna đã xử lý);
    # không có volume → skip lặng lẽ, đây là hành vi đúng và đã được document.
    if "volume_ratio" in df_c.columns and "rsi" in df_c.columns:
        w_df = df_c.dropna(subset=["volume_ratio", "rsi"])
        if len(w_df) >= 20:
            rec = w_df.tail(20)
            c20  = rec["close"].values
            vr20 = rec["volume_ratio"].values
            r20  = rec["rsi"].values
            n20  = len(c20)

            low_20  = float(np.min(c20[: n20 - 3]))
            high_20 = float(np.max(c20[: n20 - 3]))
            max_vr_3 = float(np.max(vr20[-3:]))

            # Fix #2: kiểm tra range-bound — max/min ≤ 20% → đang tích lũy
            range_pct = (high_20 - low_20) / low_20 if low_20 > 0 else 1.0
            in_range  = range_pct <= 0.20

            if in_range:
                spring = (
                    float(np.min(c20[-3:])) <= low_20 * 1.03
                    and c20[-1] > low_20 * 0.97
                    and max_vr_3 >= 1.5
                    and r20[-1] < 45
                )
                if spring:
                    bull_sigs.append((
                        f"Wyckoff Spring (tich luy {range_pct*100:.0f}% range, test day, vol {max_vr_3:.1f}x — can luc ban)", 70
                    ))

                upthrust = (
                    float(np.max(c20[-3:])) >= high_20 * 0.97
                    and c20[-1] < high_20 * 1.03
                    and max_vr_3 >= 1.5
                    and r20[-1] > 55
                )
                if upthrust:
                    bear_sigs.append((
                        f"Wyckoff Upthrust (tich luy {range_pct*100:.0f}% range, test dinh, vol {max_vr_3:.1f}x — can luc mua)", 70
                    ))

    # ── 5. RSI Momentum Reversal (Elder 1993) ────────────────────────────────
    if "rsi" in df_c.columns:
        rsi_s = df_c["rsi"].dropna()
        if len(rsi_s) >= 5:
            rv = rsi_s.values
            was_oversold   = float(np.min(rv[-5:-1])) < 30
            rsi_rising     = rv[-1] > rv[-2] > rv[-3]
            if was_oversold and rsi_rising and rv[-1] < 50:
                bull_sigs.append((
                    f"RSI Oversold Exit (RSI {rv[-5:-1].min():.0f}→{rv[-1]:.0f}, dang bat len)", 63
                ))

            was_overbought = float(np.max(rv[-5:-1])) > 70
            rsi_falling    = rv[-1] < rv[-2] < rv[-3]
            if was_overbought and rsi_falling and rv[-1] > 50:
                bear_sigs.append((
                    f"RSI Overbought Exit (RSI {rv[-5:-1].max():.0f}→{rv[-1]:.0f}, dang tut xuong)", 63
                ))

    # ── 6. Volume Climax / Exhaustion (Granville 1963) ───────────────────────
    # Fix #3: volume_ratio là optional field — dropna đã đảm bảo chỉ chạy khi có
    # dữ liệu volume. Mã không có volume data → toàn bộ block 4 và 6 skip silently.
    if "volume_ratio" in df_c.columns and "rsi" in df_c.columns:
        vc_df = df_c.dropna(subset=["volume_ratio", "rsi"])
        if len(vc_df) >= 5:
            tail5 = vc_df.tail(5)
            c5  = tail5["close"].values
            vr5 = tail5["volume_ratio"].values
            r5  = tail5["rsi"].values

            if len(c5) >= 3 and c5[-3] > 0:
                drop_pct = (c5[-2] - c5[-3]) / c5[-3] * 100
                if vr5[-2] >= 2.5 and drop_pct < -3 and c5[-1] > c5[-2] and r5[-2] < 40:
                    bull_sigs.append((
                        f"Volume Selling Climax (vol {vr5[-2]:.1f}x, giam {drop_pct:.1f}%, phuc hoi — can cung)", 65
                    ))

            if len(c5) >= 3 and c5[-3] > 0:
                rise_pct = (c5[-2] - c5[-3]) / c5[-3] * 100
                if vr5[-2] >= 2.5 and rise_pct > 3 and c5[-1] < c5[-2] and r5[-2] > 60:
                    bear_sigs.append((
                        f"Volume Buying Climax (vol {vr5[-2]:.1f}x, tang {rise_pct:.1f}%, quay xuong — can cau)", 65
                    ))

    # ── Tổng hợp ─────────────────────────────────────────────────────────────
    def _fmt_sigs(sigs: list[tuple[str, int]], direction: str) -> str:
        return " | ".join(f"{n} [{direction},{c}%]" for n, c in sigs)

    def _strength(sigs: list[tuple[str, int]]) -> int:
        """Fix #4: multiplier 1 signal → 0.80 (không phải 0.65); 1 tín hiệu chất lượng
        như RSI Divergence (68%) đạt 54% > ngưỡng 40% để vào tab Đảo Chiều."""
        if not sigs:
            return 0
        avg_conf   = sum(c for _, c in sigs) / len(sigs)
        multiplier = {1: 0.80, 2: 0.92}.get(min(len(sigs), 2), 1.0)
        return min(95, round(avg_conf * multiplier))

    bull_str = _strength(bull_sigs)
    bear_str = _strength(bear_sigs)

    if bull_sigs and bull_str >= bear_str:
        all_sigs = _fmt_sigs(bull_sigs, "bull")
        if bear_sigs:
            all_sigs += " | " + _fmt_sigs(bear_sigs, "bear")
        return {"reversal_type": "bullish", "reversal_strength": bull_str, "reversal_signals": all_sigs}
    elif bear_sigs:
        all_sigs = _fmt_sigs(bear_sigs, "bear")
        if bull_sigs:
            all_sigs += " | " + _fmt_sigs(bull_sigs, "bull")
        return {"reversal_type": "bearish", "reversal_strength": bear_str, "reversal_signals": all_sigs}

    return _EMPTY


def build_reason(
    rsi: float,
    macd_hist: float,
    dist_ema_pct: float,
    tech_score: float,
    signal: str,
    risk: str,
    phase: str,
    atr_pct: float = float("nan"),
    bb_width_pct: float = float("nan"),
    volume_ratio: float = float("nan"),
    candle_patterns: list | None = None,
    chart_patterns: list | None = None,
    ai_score: float = float("nan"),
) -> str:
    """Tổng hợp lý do khuyến nghị thành văn bản ngắn gọn, dễ đọc.
    candle_patterns/chart_patterns: list[tuple(tên, hướng, tỷ_lệ_%)]
    """
    points: list[str] = []

    # ── Momentum ──────────────────────────────────────────────────────────────
    if rsi <= RSI_OVERSOLD:
        points.append(f"RSI {rsi:.0f} — vùng quá bán (cơ hội phục hồi)")
    elif rsi >= RSI_OVERBOUGHT:
        points.append(f"RSI {rsi:.0f} — vùng quá mua (cẩn thận điều chỉnh)")
    elif rsi >= 55:
        points.append(f"RSI {rsi:.0f} — đà tăng ổn định")
    elif rsi <= 45:
        points.append(f"RSI {rsi:.0f} — đà yếu, chưa có lực mua")

    if macd_hist > 0:
        points.append(f"MACD dương ({macd_hist:+.4f}) — momentum tăng")
    elif macd_hist < 0:
        points.append(f"MACD âm ({macd_hist:+.4f}) — momentum giảm")

    # ── Vị trí so EMA34 ───────────────────────────────────────────────────────
    if dist_ema_pct < -8:
        points.append(f"Giá thấp hơn EMA34 {abs(dist_ema_pct):.1f}% — vùng hỗ trợ tiềm năng")
    elif dist_ema_pct < 0:
        points.append(f"Giá dưới EMA34 {abs(dist_ema_pct):.1f}% — chưa lấy lại trung bình")
    elif dist_ema_pct > 12:
        points.append(f"Giá trên EMA34 {dist_ema_pct:.1f}% — xa vùng an toàn, dễ điều chỉnh")
    elif dist_ema_pct > 5:
        points.append(f"Giá trên EMA34 {dist_ema_pct:.1f}% — xu hướng tăng ổn định")
    else:
        points.append(f"Giá sát EMA34 ({dist_ema_pct:+.1f}%) — vùng cân bằng")

    # ── Volatility ────────────────────────────────────────────────────────────
    if not math.isnan(atr_pct):
        if atr_pct > 4.5:
            points.append(f"ATR {atr_pct:.1f}% — biến động rất cao, stop-loss rộng")
        elif atr_pct > 3.0:
            points.append(f"ATR {atr_pct:.1f}% — biến động cao")
        else:
            points.append(f"ATR {atr_pct:.1f}% — biến động ổn định")

    if not math.isnan(bb_width_pct):
        if bb_width_pct < 5:
            points.append(f"BB Width {bb_width_pct:.1f}% — đang nén (squeeze), sắp bùng nổ")
        elif bb_width_pct > 15:
            points.append(f"BB Width {bb_width_pct:.1f}% — đang giãn mạnh")

    # ── Volume ────────────────────────────────────────────────────────────────
    if not math.isnan(volume_ratio):
        if volume_ratio >= 1.5:
            points.append(f"KL {volume_ratio:.1f}x TB — xác nhận tín hiệu mạnh")
        elif volume_ratio < 0.7:
            points.append(f"KL chỉ {volume_ratio:.1f}x TB — thiếu xác nhận")

    # ── Phase (Wyckoff) ───────────────────────────────────────────────────────
    _phase_desc = {
        "Accumulation": "Wyckoff: giai đoạn tích lũy — tiền thông minh gom hàng",
        "Markup":        "Wyckoff: giai đoạn tăng — uptrend xác nhận",
        "Distribution":  "Wyckoff: giai đoạn phân phối — cảnh báo đỉnh",
        "Markdown":      "Wyckoff: giai đoạn giảm — áp lực bán lớn",
        "Neutral":       "Wyckoff: chưa xác định xu hướng",
    }
    if phase in _phase_desc:
        points.append(_phase_desc[phase])

    # ── Tổng điểm kỹ thuật + tín hiệu + rủi ro ──────────────────────────────
    _sig_label = {
        "BUY-A": "Mua mạnh", "BUY-B": "Mua", "HOLD": "Trung lập",
        "SELL-B": "Bán", "SELL-A": "Bán mạnh",
    }
    _risk_label = {"Low": "Thấp", "Medium": "Trung bình", "High": "Cao"}
    points.append(
        f"Điểm KT {tech_score:.0f}/100 — Tín hiệu {_sig_label.get(signal, signal)}"
        f" — Rủi ro {_risk_label.get(risk, risk)}"
    )

    # ── AI Score ──────────────────────────────────────────────────────────────
    if not math.isnan(ai_score):
        if ai_score >= 70:
            points.append(f"AI Score {ai_score:.0f} — LSTM tự tin tăng")
        elif ai_score <= 30:
            points.append(f"AI Score {ai_score:.0f} — LSTM cảnh báo giảm")

    # ── Mẫu nến (Bulkowski candlestick stats) ────────────────────────────────
    if candle_patterns:
        for name, direction, rate in candle_patterns:
            dir_label = "tang" if direction == "bull" else ("giam" if direction == "bear" else "can bang")
            points.append(f"Nen: {name} [{dir_label}, Bulkowski {rate}%]")

    # ── Mẫu hình giá (Bulkowski chart pattern stats) ─────────────────────────
    if chart_patterns:
        for name, direction, rate in chart_patterns:
            dir_label = "tang" if direction == "bull" else ("giam" if direction == "bear" else "trung tinh")
            points.append(f"Chart: {name} [{dir_label}, Bulkowski {rate}%]")

    return " • ".join(points) if points else "Khong du du lieu phan tich"


def _safe_float(val) -> float:
    """Chuyển giá trị sang float; trả NaN nếu None/NaN/lỗi."""
    try:
        v = float(val)
        return v if not math.isnan(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def get_latest_signals(df: pd.DataFrame, ai_score: float = float("nan"), rs_pct: float = float("nan")) -> dict:
    """Trả dict tín hiệu mới nhất từ DataFrame đã có indicators."""
    last = df.dropna(subset=["rsi", "macd_hist", "dist_ema34_pct"]).iloc[-1]
    rsi       = float(last["rsi"])
    macd_hist = float(last["macd_hist"])
    dist_ema  = float(last["dist_ema34_pct"])
    close     = float(last["close"])
    log_ret   = _safe_float(last.get("log_return"))

    atr_pct        = _safe_float(last.get("atr_pct"))
    bb_width_pct   = _safe_float(last.get("bb_width_pct"))
    volume_ratio   = _safe_float(last.get("volume_ratio"))
    ma_aligned     = int(last.get("ma_aligned", 0) or 0)
    price_trend_20d = _safe_float(last.get("price_trend_20d"))
    if math.isnan(price_trend_20d):
        price_trend_20d = 0.0
    macd_bsc          = int(last.get("macd_bars_since_cross", 999) or 999)
    weekly_macd_trend = int(last.get("weekly_macd_trend", 0) or 0)
    rs_14d            = _safe_float(last.get("rs_14d"))
    # rs_pct: ưu tiên giá trị inject từ ngoài (scan_ami_symbol tính so VNI thực),
    # fallback về rs_14d từ cột trong df (nếu đã inject vni_ret_14d trước add_all_indicators)
    _rs = rs_pct if not math.isnan(rs_pct) else rs_14d

    # Phase phải tính TRƯỚC tech_score vì là input của nó
    phase = classify_phase(rsi, dist_ema, price_trend_20d, ma_aligned)

    tech_score = calculate_tech_score(
        rsi, macd_hist, dist_ema, ma_aligned, volume_ratio,
        macd_bsc, phase, weekly_macd_trend, _rs,
    )
    signal = classify_signal(tech_score, volume_ratio, rsi)

    # BUY-A gate — nhất quán với backtester (v4):
    # ma_al==2: full bull structure (close>SMA20>SMA50)
    # RSI<65: chưa overbought
    # weekly_macd_trend>=0: không weekly bearish
    # price_trend_20d>0: momentum dương
    # phase not Distribution/Markdown: Wyckoff phase tốt
    # macd_hist>0: daily MACD xác nhận
    if signal == "BUY-A":
        rsi_ok   = rsi < 65
        wmt_ok   = weekly_macd_trend >= 0
        t20_ok   = price_trend_20d > 0
        phase_ok = phase not in ("Distribution", "Markdown")
        macd_ok  = macd_hist > 0
        if ma_aligned < 2 or not rsi_ok or not wmt_ok or not t20_ok or not phase_ok or not macd_ok:
            signal = "BUY-B"

    risk   = classify_risk(tech_score, dist_ema, atr_pct, bb_width_pct, volume_ratio)

    # Xu hướng 3–5 phiên: MACD histogram tăng liên tiếp (Granville momentum)
    _mh = df.dropna(subset=["macd_hist"])["macd_hist"]
    macd_rising = bool(len(_mh) >= 3 and _mh.iloc[-1] > _mh.iloc[-2] > _mh.iloc[-3])

    # Giá trên SMA5 liên tục 3 phiên (xác nhận xu hướng ngắn hạn)
    _c = df["close"].dropna()
    _s5 = _c.rolling(5).mean()
    price_above_sma5_3d = bool(
        len(_c) >= 5
        and all(_c.iloc[i] > _s5.iloc[i] for i in [-1, -2, -3])
    )

    candle_patterns, candle_tf = detect_candle_patterns(df)
    chart_patterns              = detect_chart_patterns(df)
    reversal                    = detect_reversals(df)

    # Format để lưu cache: icon + tên ngắn (bỏ phần ASCII trong ngoặc) + tỷ lệ
    _DIR_ICON = {"bull": "🟢", "bear": "🔴", "neutral": "🟡"}
    def _fmt_patterns(plist):
        if not plist:
            return ""
        parts = []
        for name, d, r in plist:
            short = name.split("(")[0].strip()   # bỏ phần (ascii transliteration)
            parts.append(f"{_DIR_ICON.get(d, '')} {short} {r}%")
        return " | ".join(parts)

    reason = build_reason(
        rsi=rsi, macd_hist=macd_hist, dist_ema_pct=dist_ema,
        tech_score=tech_score, signal=signal, risk=risk, phase=phase,
        atr_pct=atr_pct, bb_width_pct=bb_width_pct, volume_ratio=volume_ratio,
        candle_patterns=candle_patterns, chart_patterns=chart_patterns,
        ai_score=ai_score,
    )
    # Gắn thêm reversal signals vào reason nếu có
    if reversal["reversal_signals"]:
        rev_label = "↗ Đảo chiều tăng" if reversal["reversal_type"] == "bullish" else "↘ Đảo chiều giảm"
        reason += f" • {rev_label} (sức mạnh {reversal['reversal_strength']}%): {reversal['reversal_signals']}"

    result = {
        "close":             close,
        "rsi":               round(rsi, 2),
        "macd_hist":         round(macd_hist, 4),
        "dist_ema34_pct":    round(dist_ema, 2),
        "log_return":        round(log_ret, 4) if not math.isnan(log_ret) else 0.0,
        "tech_score":        round(tech_score, 1),
        "signal":            signal,
        "risk":              risk,
        "phase":             phase,
        "candle_patterns":   _fmt_patterns(candle_patterns),
        "candle_timeframe":  candle_tf,   # 'daily' | 'weekly' | 'none'
        "chart_patterns":    _fmt_patterns(chart_patterns),
        "reason":            reason,
        "macd_rising":         macd_rising,
        "price_above_sma5_3d": price_above_sma5_3d,
        "weekly_macd_trend":   weekly_macd_trend,   # +1 weekly up / -1 down / 0 unknown
        "rs_14d":              round(_rs, 2) if not math.isnan(_rs) else None,
        "reversal_type":     reversal["reversal_type"],
        "reversal_strength": reversal["reversal_strength"],
        "reversal_signals":  reversal["reversal_signals"],
    }
    if not math.isnan(atr_pct):
        result["atr_pct"] = round(atr_pct, 2)
    if not math.isnan(bb_width_pct):
        result["bb_width_pct"] = round(bb_width_pct, 2)
    if not math.isnan(volume_ratio):
        result["volume_ratio"] = round(volume_ratio, 2)
    return result
