"""
Backtest tín hiệu BUY-A trên lịch sử giá.

Phương pháp:
- Lấy tất cả mã trong scores_cache
- Với mỗi mã, tính lại signals trên rolling window lịch sử (giống cách hệ thống tính real-time)
- Mỗi khi tín hiệu chuyển sang BUY-A: giả lập mua tại close ngày đó + 0.15% slippage
- Đo return tại T+5, T+10, T+20 phiên sau khi trừ phí mua+bán (0.35% tổng)
- Báo cáo: expectancy, win rate, avg return, max drawdown, phân bố

Chạy: python backtest_buya.py
Kết quả: data/backtest_results.json + in tóm tắt ra console
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_PATH    = Path("data/scores_cache.json")
RESULTS_PATH  = Path("data/backtest_results.json")
FEE_TOTAL     = 0.0035   # 0.15% mua + 0.20% bán = 0.35% tổng (thực tế VN)
SLIPPAGE      = 0.0015   # 0.15% trượt giá (lệnh market khớp cao hơn close 1 tick)
HOLD_PERIODS  = [5, 10, 20]  # số phiên giữ
LOOKBACK_DAYS = 365       # lấy 1 năm lịch sử
MIN_BARS      = 60        # bỏ qua mã có ít hơn 60 phiên data
SIGNAL_COOLDOWN = 5       # không mua lại cùng mã trong vòng 5 phiên


def _load_symbols():
    data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return [r["symbol"] for r in data if r.get("symbol")]


def _fetch_history(symbol: str) -> pd.DataFrame | None:
    """Lấy lịch sử giá từ vnstock VCI."""
    try:
        from vnstock import Vnstock
        end   = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=LOOKBACK_DAYS + 30)).strftime("%Y-%m-%d")
        h = Vnstock().stock(symbol=symbol, source="VCI").quote.history(
            start=start, end=end, interval="1D"
        )
        if h is None or len(h) < MIN_BARS:
            return None
        h = h.sort_values("time").reset_index(drop=True)
        return h
    except Exception:
        return None


def _calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Tính RSI, MACD, EMA34, dist_ema34_pct, tech_score — giống utils.py."""
    close = df["close"].astype(float)

    # RSI-14 Wilder
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line  = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = macd_line - signal_line

    # EMA34
    ema34 = close.ewm(span=34, adjust=False).mean()
    df["dist_ema34_pct"] = (close - ema34) / ema34 * 100

    # Volume ratio
    vol = df["volume"].astype(float)
    df["volume_ratio"] = vol / vol.rolling(20).mean()

    # MA aligned (SMA5 > SMA20 > SMA50)
    sma5  = close.rolling(5).mean()
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    df["ma_aligned"] = ((sma5 > sma20) & (sma20 > sma50)).astype(int) * 2

    return df


def _tech_score(rsi, macd_hist, dist_ema_pct, volume_ratio, ma_aligned) -> float:
    """Tech score 0-100 — clone từ vn_invest/indicators.py."""
    score = 50.0

    # RSI component (±20)
    if rsi <= 30:   score += 20
    elif rsi <= 45: score += 10
    elif rsi >= 75: score -= 20
    elif rsi >= 60: score -= 8

    # MACD component (±15)
    if macd_hist > 0:  score += 15
    else:              score -= 15

    # Dist EMA34 component (±15)
    if -5 <= dist_ema_pct <= 5:   score += 15
    elif dist_ema_pct > 15:       score -= 15
    elif dist_ema_pct < -15:      score -= 5

    # Volume (±10)
    if volume_ratio >= 1.5:   score += 10
    elif volume_ratio >= 1.0: score += 5
    elif volume_ratio < 0.5:  score -= 10

    # MA aligned (±10)
    if ma_aligned == 2:   score += 10
    elif ma_aligned == 0: score -= 5

    return max(0.0, min(100.0, score))


def _classify(tech_score: float) -> str:
    if tech_score >= 75: return "BUY-A"
    if tech_score >= 60: return "BUY-B"
    if tech_score >= 40: return "HOLD"
    if tech_score >= 25: return "SELL-B"
    return "SELL-A"


def _run_backtest_one(symbol: str, df: pd.DataFrame) -> list[dict]:
    """Trả về danh sách các trade đã thực hiện cho 1 mã."""
    df = _calc_indicators(df.copy())
    df = df.dropna(subset=["rsi", "macd_hist", "dist_ema34_pct"]).reset_index(drop=True)

    trades = []
    last_buy_idx = -SIGNAL_COOLDOWN  # cooldown tracking

    for i in range(50, len(df) - max(HOLD_PERIODS)):
        row = df.iloc[i]
        ts = _tech_score(
            row["rsi"], row["macd_hist"], row["dist_ema34_pct"],
            row.get("volume_ratio", 1.0), row.get("ma_aligned", 0),
        )
        sig = _classify(ts)

        if sig != "BUY-A":
            continue
        if i - last_buy_idx < SIGNAL_COOLDOWN:
            continue

        entry_price = float(row["close"]) * (1 + SLIPPAGE)
        last_buy_idx = i

        trade = {
            "symbol":      symbol,
            "entry_date":  str(row.get("time", ""))[:10],
            "entry_price": round(entry_price, 4),
            "tech_score":  round(ts, 1),
            "rsi":         round(float(row["rsi"]), 1),
        }

        for n in HOLD_PERIODS:
            exit_row   = df.iloc[i + n]
            exit_price = float(exit_row["close"]) * (1 - SLIPPAGE)
            ret_pct    = (exit_price - entry_price) / entry_price * 100 - FEE_TOTAL * 100
            trade[f"ret_t{n}"] = round(ret_pct, 3)

        trades.append(trade)

    return trades


def _report(all_trades: list[dict]) -> dict:
    df = pd.DataFrame(all_trades)
    report = {"generated_at": datetime.now().isoformat(), "total_trades": len(df)}

    for n in HOLD_PERIODS:
        col = f"ret_t{n}"
        if col not in df.columns:
            continue
        rets = df[col].dropna()
        wins = (rets > 0).sum()
        report[f"T{n}"] = {
            "n":            len(rets),
            "win_rate":     round(wins / len(rets) * 100, 1) if len(rets) else 0,
            "avg_return":   round(rets.mean(), 3),
            "median_return": round(rets.median(), 3),
            "avg_win":      round(rets[rets > 0].mean(), 3) if wins else 0,
            "avg_loss":     round(rets[rets <= 0].mean(), 3) if (rets <= 0).sum() else 0,
            "expectancy":   round(rets.mean(), 3),
            "max_win":      round(rets.max(), 3),
            "max_loss":     round(rets.min(), 3),
            "pct_above_2":  round((rets > 2).mean() * 100, 1),
            "pct_below_m2": round((rets < -2).mean() * 100, 1),
        }

    # Max drawdown (equity curve T10, equally weighted)
    if "ret_t10" in df.columns:
        equity = (1 + df["ret_t10"].fillna(0) / 100).cumprod()
        roll_max = equity.cummax()
        dd = (equity - roll_max) / roll_max * 100
        report["max_drawdown_t10"] = round(dd.min(), 2)

    # Phân bố theo mã
    report["symbols_tested"] = df["symbol"].nunique()
    report["top_symbols"]    = (
        df.groupby("symbol")["ret_t10"].mean()
        .sort_values(ascending=False).head(10).round(2).to_dict()
        if "ret_t10" in df.columns else {}
    )

    return report


def main():
    symbols = _load_symbols()
    print(f"Bắt đầu backtest {len(symbols)} mã | lookback {LOOKBACK_DAYS} ngày")
    print(f"Phí + slippage: {(FEE_TOTAL + SLIPPAGE*2)*100:.2f}% round-trip\n")

    all_trades: list[dict] = []
    errors: list[str] = []

    for i, sym in enumerate(symbols, 1):
        try:
            df = _fetch_history(sym)
            if df is None:
                continue
            trades = _run_backtest_one(sym, df)
            all_trades.extend(trades)
            if i % 20 == 0 or i == len(symbols):
                print(f"  [{i}/{len(symbols)}] {sym}: {len(trades)} trades tích lũy | tổng {len(all_trades)}")
        except Exception as e:
            errors.append(f"{sym}: {e}")
        time.sleep(0.3)

    if not all_trades:
        print("Không có trade nào — kiểm tra data!")
        return

    report = _report(all_trades)
    report["errors"] = errors[:20]

    # Lưu kết quả
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # In tóm tắt
    print(f"\n{'='*55}")
    print(f"  BACKTEST BUY-A — {report['total_trades']} tín hiệu | {report['symbols_tested']} mã")
    print(f"{'='*55}")
    for n in HOLD_PERIODS:
        r = report.get(f"T{n}", {})
        if not r:
            continue
        sign = "✅" if r["avg_return"] > 0 else "❌"
        print(f"\n  Giữ T+{n:2d} phiên  ({r['n']} trades)")
        print(f"  {sign} Avg return  : {r['avg_return']:+.2f}%")
        print(f"     Win rate    : {r['win_rate']:.1f}%")
        print(f"     Expectancy  : {r['expectancy']:+.2f}%")
        print(f"     Avg win/loss: {r['avg_win']:+.2f}% / {r['avg_loss']:+.2f}%")
        print(f"     >+2% : {r['pct_above_2']:.0f}% | <-2% : {r['pct_below_m2']:.0f}%")

    if "max_drawdown_t10" in report:
        print(f"\n  Max drawdown (T10 equity): {report['max_drawdown_t10']:.1f}%")
    print(f"\n  Kết quả lưu tại: {RESULTS_PATH}")
    print(f"  Lỗi fetch: {len(errors)}/{len(symbols)} mã\n")


if __name__ == "__main__":
    main()
