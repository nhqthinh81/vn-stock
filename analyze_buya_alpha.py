"""
Phân tích alpha BUY-A theo 3 chiều:
  1. Ngành (sector từ vnstock overview)
  2. RSI range tại thời điểm BUY-A
  3. Market regime (VNINDEX trend tại ngày tín hiệu)

Chạy: python analyze_buya_alpha.py
Kết quả: data/alpha_analysis.json + in bảng ra console
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

# Patch vnai rate-limit: CleanErrorContext.__exit__ gọi sys.exit() → bắt thành Exception
try:
    from vnai.beam.quota import CleanErrorContext as _CEC
    def _safe_exit(self, exc_type, exc_val, exc_tb):
        return False
    _CEC.__exit__ = _safe_exit
except Exception:
    pass

CACHE_PATH   = Path("data/scores_cache.json")
SECTOR_CACHE = Path("data/sector_map.json")
OUT_PATH     = Path("data/alpha_analysis.json")
PROGRESS     = Path("data/alpha_progress.json")  # checkpoint để resume
FEE_TOTAL    = 0.0035
SLIPPAGE     = 0.0015
HOLD_N       = 10        # phân tích chính trên T+10
LOOKBACK     = 400       # ngày lịch sử
MIN_BARS     = 60
COOLDOWN     = 5
DELAY        = 1.1       # 1.1s/mã → ~54 req/phút, dưới ngưỡng 60
RATE_WAIT    = 65        # chờ 65s khi bị rate limit rồi retry


# ── 1. Fetch ngành ────────────────────────────────────────────────────────────

def load_sector_map(symbols: list[str]) -> dict[str, str]:
    """Trả về {symbol: sector}. Cache vào file để không fetch lại."""
    if SECTOR_CACHE.exists():
        cached = json.loads(SECTOR_CACHE.read_text(encoding="utf-8"))
        missing = [s for s in symbols if s not in cached]
        if not missing:
            return cached
    else:
        cached = {}
        missing = symbols

    print(f"Fetch ngành cho {len(missing)} mã từ vnstock...")
    from vnstock import Vnstock
    for i, sym in enumerate(missing, 1):
        try:
            ov = Vnstock().stock(symbol=sym, source="VCI").company.overview()
            sector = str(ov["sector"].iloc[0]) if not ov.empty and "sector" in ov.columns else "Khác"
            cached[sym] = sector
        except Exception:
            cached[sym] = "Khác"
        if i % 30 == 0:
            print(f"  [{i}/{len(missing)}] sector fetch...")
            SECTOR_CACHE.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.2)

    SECTOR_CACHE.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    print(f"  Xong. {len(cached)} mã có ngành.")
    return cached


# ── 2. Fetch VNINDEX để tính market regime ────────────────────────────────────

def load_vnindex() -> pd.DataFrame:
    """Trả về DataFrame VNINDEX với cột date + close + regime."""
    print("Fetch VNINDEX lịch sử...")
    try:
        import requests, time as _t
        end_ts   = int(_t.time())
        start_ts = end_ts - 86400 * (LOOKBACK + 30)
        r = requests.get(
            f"https://histdatafeed.vps.com.vn/tradingview/history"
            f"?symbol=VNINDEX&resolution=D&from={start_ts}&to={end_ts}",
            timeout=15,
        )
        d = r.json()
        df = pd.DataFrame({"ts": d["t"], "close": d["c"]})
        df["date"] = pd.to_datetime(df["ts"], unit="s").dt.strftime("%Y-%m-%d")
        # Regime: SMA20 trend của VNINDEX
        df["sma20"] = df["close"].rolling(20).mean()
        df["sma50"] = df["close"].rolling(50).mean()
        df["regime"] = "neutral"
        df.loc[df["close"] > df["sma20"], "regime"] = "bull"
        df.loc[df["close"] < df["sma20"], "regime"] = "bear"
        print(f"  VNINDEX: {len(df)} phiên, regime bull/neutral/bear ok")
        return df.set_index("date")
    except Exception as e:
        print(f"  VNINDEX fetch lỗi: {e} — dùng regime='unknown'")
        return pd.DataFrame()


# ── 3. Tái dựng signals từ lịch sử giá ───────────────────────────────────────

def fetch_history(symbol: str) -> pd.DataFrame | None:
    from vnstock import Vnstock
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=LOOKBACK + 30)).strftime("%Y-%m-%d")
    for attempt in range(3):
        try:
            h = Vnstock().stock(symbol=symbol, source="VCI").quote.history(
                start=start, end=end, interval="1D"
            )
            if h is None or len(h) < MIN_BARS:
                return None
            return h.sort_values("time").reset_index(drop=True)
        except SystemExit:
            # Rate limit → vnstock gọi sys.exit(); bắt lại và chờ
            print(f"  [{symbol}] Rate limit — chờ {RATE_WAIT}s (attempt {attempt+1}/3)...")
            time.sleep(RATE_WAIT)
        except Exception:
            return None
    return None


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].astype(float)
    # RSI-14 Wilder
    delta = close.diff()
    g = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    l = (-delta).clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + g / l.replace(0, np.nan))
    # MACD
    e12 = close.ewm(span=12, adjust=False).mean()
    e26 = close.ewm(span=26, adjust=False).mean()
    df["macd_hist"] = (e12 - e26) - (e12 - e26).ewm(span=9, adjust=False).mean()
    # EMA34
    ema34 = close.ewm(span=34, adjust=False).mean()
    df["dist_ema34"] = (close - ema34) / ema34 * 100
    # Volume ratio
    vol = df["volume"].astype(float)
    df["vol_ratio"] = vol / vol.rolling(20).mean()
    # MA aligned
    s5  = close.rolling(5).mean()
    s20 = close.rolling(20).mean()
    s50 = close.rolling(50).mean()
    df["ma_al"] = ((s5 > s20) & (s20 > s50)).astype(int) * 2
    return df


def tech_score(rsi, macd_hist, dist_ema, vol_ratio, ma_al) -> float:
    s = 50.0
    if rsi <= 30:   s += 20
    elif rsi <= 45: s += 10
    elif rsi >= 75: s -= 20
    elif rsi >= 60: s -= 8
    s += 15 if macd_hist > 0 else -15
    if -5 <= dist_ema <= 5:   s += 15
    elif dist_ema > 15:       s -= 15
    elif dist_ema < -15:      s -= 5
    if vol_ratio >= 1.5:   s += 10
    elif vol_ratio >= 1.0: s += 5
    elif vol_ratio < 0.5:  s -= 10
    if ma_al == 2:   s += 10
    elif ma_al == 0: s -= 5
    return max(0.0, min(100.0, s))


def run_one(symbol: str, df: pd.DataFrame, vni: pd.DataFrame) -> list[dict]:
    df = calc_indicators(df.copy()).dropna(
        subset=["rsi", "macd_hist", "dist_ema34"]
    ).reset_index(drop=True)

    trades = []
    last_buy = -COOLDOWN

    for i in range(50, len(df) - HOLD_N - 1):
        row = df.iloc[i]
        ts  = tech_score(
            row["rsi"], row["macd_hist"], row["dist_ema34"],
            row.get("vol_ratio", 1.0), row.get("ma_al", 0),
        )
        if ts < 75:
            continue
        if i - last_buy < COOLDOWN:
            continue
        last_buy = i

        entry = float(row["close"]) * (1 + SLIPPAGE)
        exit_ = float(df.iloc[i + HOLD_N]["close"]) * (1 - SLIPPAGE)
        ret   = (exit_ - entry) / entry * 100 - FEE_TOTAL * 100

        date_str = str(row.get("time", ""))[:10]
        _reg = vni.loc[date_str, "regime"] if (not vni.empty and date_str in vni.index) else "unknown"
        regime = str(_reg.iloc[0]) if hasattr(_reg, "iloc") else str(_reg)

        rsi_val = float(row["rsi"])
        rsi_bin = (
            "RSI<35 (oversold)"  if rsi_val < 35 else
            "RSI 35-50"          if rsi_val < 50 else
            "RSI 50-60"          if rsi_val < 60 else
            "RSI 60-65"          if rsi_val < 65 else
            "RSI≥65 (overbought)"
        )

        dist = float(row["dist_ema34"])
        dist_bin = (
            "Dưới EMA34 >5%"   if dist < -5  else
            "Gần EMA34 ±5%"    if dist <= 5  else
            "Trên EMA34 5-15%" if dist <= 15 else
            "Trên EMA34 >15%"
        )

        trades.append({
            "symbol":   symbol,
            "date":     date_str,
            "ret_t10":  round(ret, 3),
            "rsi":      round(rsi_val, 1),
            "rsi_bin":  rsi_bin,
            "dist_bin": dist_bin,
            "regime":   regime,
            "ts":       round(ts, 1),
        })
    return trades


# ── 4. Phân tích alpha ────────────────────────────────────────────────────────

def alpha_table(df: pd.DataFrame, group_col: str, mkt_avg: float) -> list[dict]:
    rows = []
    for grp, sub in df.groupby(group_col):
        rets = sub["ret_t10"]
        n    = len(rets)
        if n < 5:
            continue
        avg  = rets.mean()
        rows.append({
            "group":       str(grp),
            "n":           n,
            "win_rate":    round((rets > 0).mean() * 100, 1),
            "avg_return":  round(avg, 2),
            "alpha":       round(avg - mkt_avg, 2),
            "median":      round(rets.median(), 2),
            "max_win":     round(rets.max(), 2),
            "max_loss":    round(rets.min(), 2),
        })
    return sorted(rows, key=lambda x: x["alpha"], reverse=True)


def print_table(title: str, rows: list[dict]):
    print(f"\n{'─'*65}")
    print(f"  {title}")
    print(f"{'─'*65}")
    print(f"  {'Nhóm':<26} {'N':>4} {'WinRate':>8} {'AvgRet':>8} {'Alpha':>8}")
    print(f"  {'─'*26} {'─'*4} {'─'*8} {'─'*8} {'─'*8}")
    for r in rows:
        sign = "✅" if r["alpha"] > 0 else "❌"
        print(f"  {sign} {r['group']:<24} {r['n']:>4} {r['win_rate']:>7.1f}% {r['avg_return']:>+7.2f}% {r['alpha']:>+7.2f}%")


# ── 5. Main ───────────────────────────────────────────────────────────────────

def main():
    symbols = [r["symbol"] for r in json.loads(CACHE_PATH.read_text(encoding="utf-8"))]
    print(f"Phân tích alpha BUY-A — {len(symbols)} mã\n")

    sector_map = load_sector_map(symbols)
    vni        = load_vnindex()

    # Load checkpoint nếu đã chạy dở
    ckpt: dict = {}
    if PROGRESS.exists():
        try:
            ckpt = json.loads(PROGRESS.read_text(encoding="utf-8"))
            print(f"  Resume từ checkpoint: {len(ckpt.get('done', []))} mã đã xong")
        except Exception:
            ckpt = {}
    done_set    = set(ckpt.get("done", []))
    all_trades  = ckpt.get("trades", [])
    all_rets    = ckpt.get("rets", [])

    print(f"\nFetch lịch sử giá & tính signals ({len(symbols)} mã, delay {DELAY}s)...")
    for i, sym in enumerate(symbols, 1):
        if sym in done_set:
            continue
        try:
            df = fetch_history(sym)
            if df is None:
                done_set.add(sym)
                continue
            trades = run_one(sym, df, vni)
            sector = sector_map.get(sym, "Khác")
            for t in trades:
                t["sector"] = sector
            all_trades.extend(trades)
            df2 = calc_indicators(df.copy()).dropna(subset=["rsi"])
            for j in range(50, len(df2) - HOLD_N - 1):
                ep = float(df2.iloc[j]["close"]) * (1 + SLIPPAGE)
                xp = float(df2.iloc[j + HOLD_N]["close"]) * (1 - SLIPPAGE)
                all_rets.append((xp - ep) / ep * 100 - FEE_TOTAL * 100)
            done_set.add(sym)

            if i % 20 == 0 or i == len(symbols):
                print(f"  [{i}/{len(symbols)}] {sym}: {len(trades)} BUY-A | tổng {len(all_trades)}")
                # Lưu checkpoint mỗi 20 mã
                PROGRESS.write_text(json.dumps(
                    {"done": list(done_set), "trades": all_trades, "rets": all_rets},
                    ensure_ascii=False, default=str
                ), encoding="utf-8")
        except Exception as e:
            print(f"  {sym}: lỗi — {e}")
            done_set.add(sym)
        time.sleep(DELAY)

    if not all_trades:
        print("Không có trade nào!")
        return

    df_trades = pd.DataFrame(all_trades)
    mkt_avg   = float(np.mean(all_rets)) if all_rets else 0.0
    buya_avg  = df_trades["ret_t10"].mean()

    print(f"\n{'='*65}")
    print(f"  TỔNG KẾT: {len(df_trades)} tín hiệu BUY-A | {df_trades['symbol'].nunique()} mã")
    print(f"  Market avg T+{HOLD_N}: {mkt_avg:+.2f}%")
    print(f"  BUY-A avg T+{HOLD_N} : {buya_avg:+.2f}%  (alpha {buya_avg - mkt_avg:+.2f}%)")
    print(f"  Win rate            : {(df_trades['ret_t10'] > 0).mean()*100:.1f}%")
    print(f"{'='*65}")

    # Phân tích 3 chiều
    by_sector  = alpha_table(df_trades, "sector",  mkt_avg)
    by_rsi     = alpha_table(df_trades, "rsi_bin", mkt_avg)
    by_regime  = alpha_table(df_trades, "regime",  mkt_avg)
    by_dist    = alpha_table(df_trades, "dist_bin", mkt_avg)

    print_table(f"ALPHA THEO NGÀNH (vs market avg {mkt_avg:+.2f}%)", by_sector)
    print_table(f"ALPHA THEO RSI RANGE", by_rsi)
    print_table(f"ALPHA THEO MARKET REGIME (VNINDEX vs SMA20)", by_regime)
    print_table(f"ALPHA THEO VỊ TRÍ GIÁ vs EMA34", by_dist)

    # Tổ hợp tốt nhất — BUY-A thắng lớn nhất khi nào?
    print(f"\n{'─'*65}")
    print("  TOP 10 TỔ HỢP ĐIỀU KIỆN TỐT NHẤT (regime + RSI bin)")
    print(f"{'─'*65}")
    combo = (
        df_trades.groupby(["regime", "rsi_bin"])["ret_t10"]
        .agg(["mean", "count", lambda x: (x > 0).mean() * 100])
        .rename(columns={"mean": "avg", "count": "n", "<lambda_0>": "wr"})
        .reset_index()
    )
    combo["alpha"] = combo["avg"] - mkt_avg
    combo = combo[combo["n"] >= 5].sort_values("alpha", ascending=False).head(10)
    print(f"  {'Regime':<10} {'RSI':<22} {'N':>4} {'WinRate':>8} {'Alpha':>8}")
    for _, r in combo.iterrows():
        sign = "✅" if r["alpha"] > 0 else "❌"
        print(f"  {sign} {r['regime']:<9} {r['rsi_bin']:<22} {int(r['n']):>4} {r['wr']:>7.1f}% {r['alpha']:>+7.2f}%")

    # Lưu kết quả
    result = {
        "generated_at": datetime.now().isoformat(),
        "total_buya_signals": len(df_trades),
        "symbols_tested": df_trades["symbol"].nunique(),
        "hold_period": HOLD_N,
        "market_avg": round(mkt_avg, 3),
        "buya_avg":   round(buya_avg, 3),
        "buya_alpha": round(buya_avg - mkt_avg, 3),
        "buya_winrate": round((df_trades["ret_t10"] > 0).mean() * 100, 1),
        "by_sector":  by_sector,
        "by_rsi":     by_rsi,
        "by_regime":  by_regime,
        "by_dist_ema": by_dist,
        "best_combos": combo.to_dict(orient="records"),
        "raw_trades": df_trades.to_dict(orient="records"),
    }
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n  Kết quả lưu tại: {OUT_PATH}")
    print(f"  Raw trades: {len(df_trades)} dòng trong alpha_analysis.json\n")


if __name__ == "__main__":
    main()
