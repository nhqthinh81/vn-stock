"""
Backtest tối giản — đo win rate thực tế của tech_score signal trên dữ liệu VN.

Phương pháp:
  - Với mỗi mã trong history_by_ticker/: tính indicators một lần trên full history
  - Tại mỗi bar t (từ bar 60 đến bar n - forward_days):
      * Đọc signal tại t từ indicators đã tính
      * Tính forward return tại t + forward_days
  - Gom kết quả: win rate, avg return, median return theo signal class
  - "Win" = forward_return > 0 với BUY, < 0 với SELL

Lưu kết quả vào data/backtest_results.json.
"""
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_AMI_DIR      = Path(__file__).parent.parent.parent  # fallback, overridden below
_RESULTS_PATH = Path(__file__).parent.parent / "data" / "backtest_results.json"

# Import lazy để tránh circular
def _get_ami_dir() -> Path:
    import os
    return Path(os.getenv("AMIBROKER_HIST_DIR", r"C:\AmibrokerData\history_by_ticker"))


def _parse_ami_date(date_val) -> str:
    s = str(int(date_val)).zfill(8)
    return f"20{s[2:4]}-{s[4:6]}-{s[6:8]}"


def backtest_symbol(
    symbol: str,
    forward_days: int = 10,
    min_history: int = 80,
    regime_series: "pd.Series | None" = None,
    vni_ret_series: "pd.Series | None" = None,
) -> list[dict]:
    """
    Trả list records: [{"signal": "BUY-A", "fwd_return": 3.5}, ...]

    Cải tiến v2 + v3:
      - Sol 1: Signal Persistence — chỉ count khi signal giữ nguyên ≥ 2 ngày
      - Sol 2: MACD Freshness — macd_bars_since_cross trong calculate_tech_score
      - Sol 3: Volume Gate — classify_signal nhận volume_ratio
      - Sol 4: Market Regime — lọc BUY/SELL theo VNI vs SMA50
      - Sol 5 (v3): Wyckoff Phase per-bar → tech_score
      - Sol 6 (v3): Elder Weekly MACD per-bar → tech_score
      - Sol 7 (v3): Relative Strength vs VNI per-bar → tech_score
    """
    from .indicators import add_all_indicators, calculate_tech_score, classify_signal, classify_phase
    from .screener import _parse_ami_date as _parse_date

    path = _get_ami_dir() / f"{symbol.upper()}.csv"
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path, header=0)
        df.columns = [c.strip() for c in df.columns]
        df["Date"] = pd.to_datetime(df["Date"].apply(_parse_date), errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"])
        if len(df) < min_history + forward_days:
            return []
        df = df.rename(columns={"Open":"open","High":"high","Low":"low",
                                  "Close":"close","Volume":"volume"})
        # Inject VNI return 14d trước add_all_indicators để rs_14d được tính trong indicators
        if vni_ret_series is not None and not vni_ret_series.empty:
            df["vni_ret_14d"] = df["Date"].map(vni_ret_series).fillna(np.nan)
        df = add_all_indicators(df)
    except Exception:
        return []

    records = []
    close_arr  = df["close"].values
    dates_arr  = df["Date"].values
    rsi_arr    = df["rsi"].values if "rsi" in df.columns else np.full(len(df), np.nan)
    macd_arr   = df["macd_hist"].values if "macd_hist" in df.columns else np.full(len(df), np.nan)
    dist_arr   = df["dist_ema34_pct"].values if "dist_ema34_pct" in df.columns else np.full(len(df), np.nan)
    ma_arr     = df["ma_aligned"].values if "ma_aligned" in df.columns else np.zeros(len(df), dtype=int)
    vol_arr    = df["volume_ratio"].values if "volume_ratio" in df.columns else np.full(len(df), np.nan)
    bsc_arr    = df["macd_bars_since_cross"].values if "macd_bars_since_cross" in df.columns else np.full(len(df), 999)
    wmt_arr    = df["weekly_macd_trend"].values if "weekly_macd_trend" in df.columns else np.zeros(len(df), dtype=int)
    rs_arr     = df["rs_14d"].values if "rs_14d" in df.columns else np.full(len(df), np.nan)
    trend20_arr= df["price_trend_20d"].values if "price_trend_20d" in df.columns else np.zeros(len(df))
    atr_pct_arr= df["atr_pct"].values if "atr_pct" in df.columns else np.full(len(df), np.nan)

    # Build regime lookup dict nếu có (date → "bull"/"bear"/"neutral")
    _regime_map: dict = {}
    if regime_series is not None and not regime_series.empty:
        _regime_map = regime_series.to_dict()

    n = len(df)
    prev_signal: str | None = None

    for i in range(60, n - forward_days):
        rsi  = rsi_arr[i]
        macd = macd_arr[i]
        dist = dist_arr[i]
        if math.isnan(rsi) or math.isnan(macd) or math.isnan(dist):
            prev_signal = None
            continue
        ma_al  = int(ma_arr[i]) if not math.isnan(float(ma_arr[i])) else 0
        vr     = float(vol_arr[i]) if not math.isnan(float(vol_arr[i])) else float("nan")
        mbsc   = int(bsc_arr[i]) if not math.isnan(float(bsc_arr[i])) else 999
        wmt    = int(wmt_arr[i]) if not math.isnan(float(wmt_arr[i])) else 0
        rs     = float(rs_arr[i]) if not math.isnan(float(rs_arr[i])) else float("nan")
        t20    = float(trend20_arr[i]) if not math.isnan(float(trend20_arr[i])) else 0.0

        # ATR filter: bỏ qua cổ phiếu biến động cực đoan (atr_pct > 5% = quá rủi ro)
        # Những mã này thường là penny stock hoặc bị thao túng — signal không đáng tin
        atr_p = float(atr_pct_arr[i]) if not math.isnan(float(atr_pct_arr[i])) else 0.0
        if atr_p > 5.0:
            prev_signal = None
            continue

        phase  = classify_phase(rsi, dist, t20, ma_al)
        score  = calculate_tech_score(rsi, macd, dist, ma_al, vr, mbsc, phase, wmt, rs)
        signal = classify_signal(score, vr, rsi)

        # Sol 1: Signal Persistence — signal phải duy trì ≥ 2 ngày liên tiếp
        if signal != prev_signal:
            prev_signal = signal
            continue   # ngày đầu xuất hiện signal → bỏ qua, chờ ngày tiếp theo xác nhận
        prev_signal = signal

        # Sol 4: Market Regime — lọc BUY khi bear market, SELL khi bull market
        bar_date = pd.Timestamp(dates_arr[i]) if _regime_map else None
        if _regime_map and bar_date is not None:
            regime = _regime_map.get(bar_date, "neutral")
            if signal in ("BUY-A", "BUY-B") and regime == "bear":
                continue
            if signal in ("SELL-A", "SELL-B") and regime == "bull":
                continue
        else:
            regime = "neutral"

        # BUY-A gate v4: full bull structure + RSI<65 + weekly OK + momentum + phase + MACD+
        # ma_al==2: close>SMA20>SMA50 — full bull structure
        # RSI<65: chưa overbought
        # wmt>=0: weekly MACD không bearish
        # t20>0: momentum 20d dương
        # phase not Distribution/Markdown: Wyckoff phase tốt
        # macd>0: MACD histogram dương — daily trend xác nhận
        if signal == "BUY-A":
            rsi_ok   = math.isnan(rsi) or rsi < 65
            wmt_ok   = wmt >= 0
            t20_ok   = t20 > 0
            phase_ok = phase not in ("Distribution", "Markdown")
            macd_ok  = not math.isnan(macd) and macd > 0
            if ma_al < 2 or not rsi_ok or not wmt_ok or not t20_ok or not phase_ok or not macd_ok:
                signal = "BUY-B"

        c0 = close_arr[i]
        cf = close_arr[i + forward_days]
        if c0 <= 0:
            continue
        fwd = (cf - c0) / c0 * 100
        records.append({"signal": signal, "fwd_return": round(fwd, 3)})

    return records


def run_backtest(
    symbols: Optional[list[str]] = None,
    forward_days: int = 10,
    max_symbols: int = 200,
    progress_callback=None,
) -> dict:
    """
    Chạy backtest trên danh sách mã (mặc định: toàn bộ history_by_ticker).
    Trả dict kết quả theo signal class + metadata.

    progress_callback(i, total, symbol) — dùng cho Streamlit progress bar.
    """
    from .market_regime import get_regime_series, get_vni_return_series

    ami_dir = _get_ami_dir()
    if symbols is None:
        try:
            symbols = sorted(p.stem.upper() for p in ami_dir.glob("*.csv")
                             if p.stem.upper() != "VNI")  # loại index khỏi backtest
        except Exception:
            symbols = []

    symbols = symbols[:max_symbols]
    total   = len(symbols)
    all_records: list[dict] = []

    # Load cả hai series 1 lần cho toàn bộ backtest
    regime_series  = get_regime_series(ami_dir)
    vni_ret_series = get_vni_return_series(14, ami_dir)

    for i, sym in enumerate(symbols):
        if progress_callback:
            progress_callback(i, total, sym)
        recs = backtest_symbol(
            sym, forward_days=forward_days,
            regime_series=regime_series,
            vni_ret_series=vni_ret_series,
        )
        all_records.extend(recs)

    # Tổng hợp theo signal class
    sig_classes  = ["BUY-A", "BUY-B", "HOLD", "SELL-B", "SELL-A"]
    summary: dict[str, dict] = {}
    for sig in sig_classes:
        returns = [r["fwd_return"] for r in all_records if r["signal"] == sig]
        if not returns:
            summary[sig] = {"count": 0, "win_rate": None, "avg_return": None, "median_return": None}
            continue
        is_buy  = sig.startswith("BUY")
        is_sell = sig.startswith("SELL")
        if is_buy:
            wins = [r for r in returns if r > 0]
        elif is_sell:
            wins = [r for r in returns if r < 0]
        else:
            # HOLD thắng khi giá dao động trong biên ±threshold (sideway đúng dự đoán)
            # Scale theo forward_days: T+5=±1.5%, T+10=±3%, T+20=±5%
            # Scale: T+5=±2%, T+10=±4%, T+20=±8% — phản ánh volatility thực tế VN
            hold_threshold = max(2.0, 2.0 * forward_days / 5)
            wins = [r for r in returns if -hold_threshold <= r <= hold_threshold]

        win_rate = len(wins) / len(returns) if returns else None
        summary[sig] = {
            "count":         len(returns),
            "win_rate":      round(win_rate * 100, 1) if win_rate is not None else None,
            "avg_return":    round(float(np.mean(returns)), 2),
            "median_return": round(float(np.median(returns)), 2),
            "std_return":    round(float(np.std(returns)), 2),
        }

    # Alpha = BUY-A avg_return - market_avg (avg tất cả signals = proxy cho market return)
    # VN market: SELL signal không dự báo giá xuống ngắn hạn do upward bias + mean reversion mạnh
    # Dùng market_avg thay vì SELL-A avg để đo alpha thực sự của BUY-A so với "mua bừa"
    buy_a_avg   = summary.get("BUY-A",  {}).get("avg_return")
    sell_a_avg  = summary.get("SELL-A", {}).get("avg_return")
    all_returns = [r["fwd_return"] for r in all_records]
    market_avg  = round(float(np.mean(all_returns)), 2) if all_returns else None
    alpha       = round(buy_a_avg - market_avg, 2) if (buy_a_avg is not None and market_avg is not None) else None
    # Giữ signal_edge (BUY-A - SELL-A) cho reference, nhưng dùng alpha là metric chính
    edge = round(buy_a_avg - sell_a_avg, 2) if (buy_a_avg is not None and sell_a_avg is not None) else None

    result = {
        "summary":         summary,
        "forward_days":    forward_days,
        "symbols_scanned": total,
        "total_signals":   len(all_records),
        "market_avg_return": market_avg,   # avg return khi mua bừa bất kỳ mã
        "buy_a_alpha":     alpha,          # BUY-A avg - market_avg: alpha thực sự (metric chính VN)
        "signal_edge":     edge,           # BUY-A - SELL-A: reference (thường âm trong VN)
        "computed_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filters_applied": [
            "signal_persistence_2d",
            "macd_freshness",
            "volume_gate_vn_adjusted(>4x)",
            "atr_filter(<5%)",
            "buya_gate(ma_al==2+rsi<65+wmt>=0+t20>0+phase)",
            "phase_bonus",
            "weekly_macd(w=±8)",
            "rs_vs_vni(w=±8)" if not vni_ret_series.empty else "rs_skipped(no_VNI)",
            "market_regime" if not regime_series.empty else "market_regime_skipped(no_VNI)",
        ],
    }
    _save_results(result)
    return result


def _save_results(data: dict) -> None:
    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_results() -> Optional[dict]:
    if _RESULTS_PATH.exists():
        try:
            return json.loads(_RESULTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None
