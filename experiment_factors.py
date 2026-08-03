# -*- coding: utf-8 -*-
"""
Kiểm định 3 factor hiện đại chưa áp dụng — dùng data AMI local, không gọi API:

  1. Cross-sectional momentum rank (Jegadeesh-Titman):
     percentile return 63 phiên (3 tháng) so với toàn thị trường tại từng ngày.
     Test: config E (+bull, giá≥10, trail12) + mom_pct >= 50/70.

  2. Market breadth regime: % mã có close > SMA50 tại từng ngày.
     Test: config E + breadth >= 40/50/60 (thay/bổ sung bull gate VNI).

  3. RSI-2 mean reversion (Connors) — chiến thuật độc lập:
     entry khi RSI(2) < 10 & close > SMA50 & giá >= 10, cooldown 5 phiên.
     Đo fwd T+5/T+10 hold + trail12, so market avg.

Chạy: python experiment_factors.py [--max N]
Kết quả: data/experiment_factors.json
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

from vn_invest.backtester import backtest_symbol, simulate_exit, _get_ami_dir
from vn_invest.market_regime import get_regime_series, get_vni_return_series

RESULTS_PATH = Path(__file__).parent / "data" / "experiment_factors.json"
FORWARD_DAYS = 10
MIN_PRICE    = 10.0


def _parse_ami_date(v) -> str:
    s = str(int(v)).zfill(8)
    return f"20{s[2:4]}-{s[4:6]}-{s[6:8]}"


def load_all_closes(ami_dir: Path, symbols: list[str]) -> dict[str, pd.Series]:
    """sym → Series close indexed by date string YYYY-MM-DD."""
    out = {}
    for sym in symbols:
        try:
            df = pd.read_csv(ami_dir / f"{sym}.csv")
            df.columns = [c.strip() for c in df.columns]
            df["d"] = df["Date"].apply(_parse_ami_date)
            s = pd.to_numeric(df["Close"], errors="coerce")
            ser = pd.Series(s.values, index=df["d"].values).dropna()
            ser = ser[~ser.index.duplicated(keep="last")].sort_index()
            if len(ser) >= 120:
                out[sym] = ser
        except Exception:
            continue
    return out


def build_factor_tables(closes: dict[str, pd.Series]):
    """Trả (mom_pct_df, breadth_series).
    mom_pct: DataFrame date×sym percentile (0-1) của return 63 phiên.
    breadth: Series date → % mã close > SMA50.

    Tính per-series TRƯỚC rồi mới ghép — union index của các mã có ngày lỗ chỗ
    (mỗi file AMI thiếu ngày khác nhau) sẽ phá rolling nếu tính trên DataFrame chung."""
    ret_mom, above = {}, {}
    for sym, ser in closes.items():
        # 252 phiên = 12 tháng daily (Jegadeesh-Titman 12M)
        ret_mom[sym] = ser.pct_change(252, fill_method=None)
        ma50 = ser.rolling(50).mean()
        above[sym] = (ser > ma50).astype(float).where(ma50.notna())
    mom_pct = pd.DataFrame(ret_mom).sort_index().rank(axis=1, pct=True)
    breadth = pd.DataFrame(above).sort_index().mean(axis=1) * 100   # mean bỏ qua NaN
    return mom_pct, breadth


def _grp_stats(rets: list[float], market_avg: float | None = None) -> dict:
    if not rets:
        return {"n": 0}
    wins = sum(1 for x in rets if x > 0)
    d = {"n": len(rets), "win": round(wins/len(rets)*100, 1),
         "avg": round(float(np.mean(rets)), 2),
         "median": round(float(np.median(rets)), 2)}
    if market_avg is not None:
        d["alpha"] = round(d["avg"] - market_avg, 2)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=440)
    args = ap.parse_args()

    ami_dir = _get_ami_dir()
    symbols = sorted(p.stem.upper() for p in ami_dir.glob("*.csv")
                     if p.stem.upper() != "VNI")[:args.max]
    print(f"Factor experiment — {len(symbols)} ma")

    t0 = time.time()
    closes = load_all_closes(ami_dir, symbols)
    mom_pct, breadth = build_factor_tables(closes)
    print(f"Closes: {len(closes)} ma | mom table {mom_pct.shape} | breadth {len(breadth)} ngay ({time.time()-t0:.0f}s)")

    regime_series  = get_regime_series(ami_dir)
    vni_ret_series = get_vni_return_series(14, ami_dir)
    regime_start   = str(regime_series.index.min())[:10] if not regime_series.empty else "2025-03-01"

    # ── Records từ backtester (như experiment_strategy) ──
    all_records = []
    for i, sym in enumerate(symbols, 1):
        recs = backtest_symbol(sym, forward_days=FORWARD_DAYS,
                               regime_series=regime_series,
                               vni_ret_series=vni_ret_series)
        all_records.extend(recs)
        if i % 100 == 0 or i == len(symbols):
            print(f"  [{i}/{len(symbols)}] {len(all_records)} records ({time.time()-t0:.0f}s)")

    # Join factor vào record
    for r in all_records:
        sym, d = r.get("symbol"), r.get("date")
        try:
            r["mom_pct"] = float(mom_pct.at[d, sym]) if sym in mom_pct.columns else float("nan")
        except Exception:
            r["mom_pct"] = float("nan")
        try:
            r["breadth"] = float(breadth.at[d])
        except Exception:
            r["breadth"] = float("nan")

    out = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "symbols": len(symbols), "forward_days": FORWARD_DAYS,
           "regime_start": regime_start, "analyses": {}}

    def base_E(recs):
        """Config E: BUY-A + bull + giá >= MIN_PRICE, exit trail12."""
        return [r for r in recs if r["signal"] == "BUY-A" and r.get("regime") == "bull"
                and r["entry_close"] >= MIN_PRICE]

    for scope_name, scope in [("full", all_records),
                              (f"recent>={regime_start}",
                               [r for r in all_records if r.get("date", "") >= regime_start])]:
        print(f"\n=== {scope_name} — {len(scope)} records ===")
        market_avg = float(np.mean([r["fwd_trail12"] for r in scope])) if scope else None
        an = {"market_avg": round(market_avg, 2) if market_avg is not None else None}

        # ── 1. Momentum rank trên nền config E ──
        E = base_E(scope)
        print(f"  Config E goc: {len(E)} lenh")
        rows = [("E_goc", E)]
        for th in (0.5, 0.7):
            rows.append((f"E+mom>={int(th*100)}", [r for r in E if r.get("mom_pct", 0) >= th]))
        # momentum standalone (không cần BUY-A): bull + giá + mom>=0.8
        rows.append(("mom80_bull_standalone",
                     [r for r in scope if r.get("regime") == "bull"
                      and r["entry_close"] >= MIN_PRICE and r.get("mom_pct", 0) >= 0.8]))
        print(f"  {'Variant':24s} {'n':>5s} {'win%':>6s} {'avg%':>7s} {'alpha':>7s}")
        for name, recs in rows:
            st = _grp_stats([r["fwd_trail12"] for r in recs], market_avg)
            an[name] = st
            if st["n"]:
                print(f"  {name:24s} {st['n']:>5d} {st['win']:>6.1f} {st['avg']:>+7.2f} {st['alpha']:>+7.2f}")
            else:
                print(f"  {name:24s} {'0':>5s}")

        # ── 2. Breadth gate trên nền config E ──
        print(f"  {'Breadth gate':24s} {'n':>5s} {'win%':>6s} {'avg%':>7s} {'alpha':>7s}")
        for th in (40, 50, 60):
            recs = [r for r in E if r.get("breadth", 0) >= th]
            st = _grp_stats([r["fwd_trail12"] for r in recs], market_avg)
            an[f"E+breadth>={th}"] = st
            if st["n"]:
                print(f"  E+breadth>={th:<12d} {st['n']:>5d} {st['win']:>6.1f} {st['avg']:>+7.2f} {st['alpha']:>+7.2f}")
            else:
                print(f"  E+breadth>={th:<12d} {'0':>5s}")
        out["analyses"][scope_name] = an

    # ── 3. RSI-2 mean reversion — độc lập, tính từ closes ──
    print("\n=== RSI-2 Connors mean reversion (doc lap) ===")
    rsi2_trades = []   # (date, sym, fwd5, fwd10, trail12)
    for sym, ser in closes.items():
        c = ser.values.astype(float)
        n = len(c)
        if n < 120:
            continue
        delta = np.diff(c, prepend=c[0])
        gain = np.clip(delta, 0, None)
        loss = np.clip(-delta, 0, None)
        ag = pd.Series(gain).ewm(alpha=1/2, adjust=False).mean().values
        al = pd.Series(loss).ewm(alpha=1/2, adjust=False).mean().values
        rs = np.divide(ag, al, out=np.full_like(ag, np.inf), where=al != 0)
        rsi2 = 100 - 100 / (1 + rs)
        sma50 = pd.Series(c).rolling(50).mean().values
        last_entry = -10
        for i in range(60, n - 10):
            if i - last_entry < 5:
                continue
            if (rsi2[i] < 10 and c[i] > sma50[i] and c[i] >= MIN_PRICE):
                fwd5  = (c[i+5]  - c[i]) / c[i] * 100
                fwd10 = (c[i+10] - c[i]) / c[i] * 100
                tr12  = simulate_exit(c, i, 10, trail_pct=0.12)
                rsi2_trades.append({"date": ser.index[i], "sym": sym,
                                    "fwd5": fwd5, "fwd10": fwd10, "trail12": tr12})
                last_entry = i

    out["rsi2"] = {}
    for scope_name, cond in [("full", lambda d: True),
                             (f"recent>={regime_start}", lambda d: d >= regime_start)]:
        recs = [t for t in rsi2_trades if cond(t["date"])]
        # market avg cùng kỳ từ records backtester (fwd_hold) để so
        mkt = [r["fwd_hold"] for r in all_records
               if cond(r.get("date", ""))]
        mkt_avg = float(np.mean(mkt)) if mkt else None
        st5  = _grp_stats([t["fwd5"]    for t in recs])
        st10 = _grp_stats([t["fwd10"]   for t in recs], mkt_avg)
        sttr = _grp_stats([t["trail12"] for t in recs], mkt_avg)
        out["rsi2"][scope_name] = {"t5": st5, "t10": st10, "trail12": sttr,
                                   "market_avg_t10": round(mkt_avg, 2) if mkt_avg else None}
        print(f"  [{scope_name}] n={st10.get('n',0)}")
        if st10.get("n"):
            print(f"    T+5 : win {st5['win']}%  avg {st5['avg']:+.2f}%")
            print(f"    T+10: win {st10['win']}%  avg {st10['avg']:+.2f}%  alpha {st10['alpha']:+.2f}% (mkt {mkt_avg:+.2f}%)")
            print(f"    trail12: win {sttr['win']}%  avg {sttr['avg']:+.2f}%  alpha {sttr['alpha']:+.2f}%")

    RESULTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nLuu: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
