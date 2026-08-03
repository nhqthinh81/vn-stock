# -*- coding: utf-8 -*-
"""
Vòng 2 — tìm filter alpha cao quanh champion E60 (BUY-A + bull + giá≥10 + breadth≥60).
Data daily 399 mã. Các filter kiểm định:

  1. Breadth sweep: ngưỡng 50/60/65/70 + breadth RISING (tăng so 5 phiên trước)
  2. 52-week high proximity (George-Hwang): khoảng cách đến đỉnh 252 phiên
  3. Low-volatility anomaly: vol 20 phiên cross-sectional tercile
  4. Short-term pullback: ret 5 phiên percentile thấp (mua nhịp chỉnh trong uptrend)
  5. Horizon: champion đo thêm T+20 (fwd_hold20)

Chạy: python experiment_factors2.py [--max N]
Kết quả: data/experiment_factors2.json
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

from vn_invest.backtester import backtest_symbol, _get_ami_dir
from vn_invest.market_regime import get_regime_series, get_vni_return_series
from experiment_factors import load_all_closes

RESULTS_PATH = Path(__file__).parent / "data" / "experiment_factors2.json"
FORWARD_DAYS = 10
MIN_PRICE    = 10.0


def build_tables(closes: dict[str, pd.Series]):
    """Trả (breadth, breadth_chg5, dist52w_df, vol20_pct_df, ret5_pct_df, fwd20_map).
    Tính per-series trước rồi ghép (tránh NaN holes union index)."""
    above, dist52, vol20, ret5, fwd20 = {}, {}, {}, {}, {}
    for sym, ser in closes.items():
        ma50 = ser.rolling(50).mean()
        above[sym] = (ser > ma50).astype(float).where(ma50.notna())
        hi252 = ser.rolling(252, min_periods=100).max()
        dist52[sym] = (hi252 - ser) / hi252 * 100          # % dưới đỉnh 52w
        ret = ser.pct_change(fill_method=None)
        vol20[sym] = ret.rolling(20).std() * 100           # vol 20 phiên (%/ngày)
        ret5[sym]  = ser.pct_change(5, fill_method=None) * 100
        # forward return T+20 (hold cứng)
        f = ser.shift(-20) / ser - 1
        fwd20[sym] = f * 100

    breadth      = pd.DataFrame(above).sort_index().mean(axis=1) * 100
    breadth_chg5 = breadth - breadth.shift(5)
    dist_df  = pd.DataFrame(dist52).sort_index()
    vol_df   = pd.DataFrame(vol20).sort_index()
    ret5_df  = pd.DataFrame(ret5).sort_index()
    # cross-sectional percentile theo ngày
    vol_pct  = vol_df.rank(axis=1, pct=True)
    ret5_pct = ret5_df.rank(axis=1, pct=True)
    fwd20_df = pd.DataFrame(fwd20).sort_index()
    return breadth, breadth_chg5, dist_df, vol_pct, ret5_pct, fwd20_df


def _stats(rets, market_avg=None):
    if not rets:
        return {"n": 0}
    wins = sum(1 for x in rets if x > 0)
    d = {"n": len(rets), "win": round(wins / len(rets) * 100, 1),
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
    print(f"Factor v2 — {len(symbols)} ma")

    t0 = time.time()
    closes = load_all_closes(ami_dir, symbols)
    breadth, br_chg5, dist_df, vol_pct, ret5_pct, fwd20_df = build_tables(closes)
    print(f"Tables xong ({time.time()-t0:.0f}s)")

    regime_series  = get_regime_series(ami_dir)
    vni_ret_series = get_vni_return_series(14, ami_dir)
    regime_start   = str(regime_series.index.min())[:10] if not regime_series.empty else "2025-03-01"

    all_records = []
    for i, sym in enumerate(symbols, 1):
        recs = backtest_symbol(sym, forward_days=FORWARD_DAYS,
                               regime_series=regime_series,
                               vni_ret_series=vni_ret_series)
        all_records.extend(recs)
        if i % 100 == 0 or i == len(symbols):
            print(f"  [{i}/{len(symbols)}] {len(all_records)} records ({time.time()-t0:.0f}s)")

    # Join factor
    def _at(df, d, s):
        try:
            v = df.at[d, s]
            return float(v) if pd.notna(v) else float("nan")
        except Exception:
            return float("nan")

    for r in all_records:
        s, d = r["symbol"], r["date"]
        try:
            r["breadth"] = float(breadth.at[d]) if d in breadth.index else float("nan")
        except Exception:
            r["breadth"] = float("nan")
        try:
            r["br_chg5"] = float(br_chg5.at[d]) if d in br_chg5.index else float("nan")
        except Exception:
            r["br_chg5"] = float("nan")
        r["dist52w"]  = _at(dist_df, d, s)
        r["vol_pct"]  = _at(vol_pct, d, s)
        r["ret5_pct"] = _at(ret5_pct, d, s)
        r["fwd20"]    = _at(fwd20_df, d, s)

    recent = [r for r in all_records if r.get("date", "") >= regime_start]

    def base_E(recs):
        return [r for r in recs if r["signal"] == "BUY-A" and r.get("regime") == "bull"
                and r["entry_close"] >= MIN_PRICE]

    out = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "symbols": len(symbols), "regime_start": regime_start, "scopes": {}}

    for scope_name, scope in [("full", all_records), (f"recent>={regime_start}", recent)]:
        market10 = float(np.mean([r["fwd_trail12"] for r in scope])) if scope else None
        _f20 = [r["fwd20"] for r in scope if not np.isnan(r.get("fwd20", float("nan")))]
        market20 = float(np.mean(_f20)) if _f20 else None
        E = base_E(scope)

        variants = [
            ("E_goc",            E),
            ("E+br50",           [r for r in E if r.get("breadth", 0) >= 50]),
            ("E+br60",           [r for r in E if r.get("breadth", 0) >= 60]),
            ("E+br65",           [r for r in E if r.get("breadth", 0) >= 65]),
            ("E+br70",           [r for r in E if r.get("breadth", 0) >= 70]),
            ("E+br60+rising",    [r for r in E if r.get("breadth", 0) >= 60
                                  and (r.get("br_chg5") or 0) > 0]),
            ("E+br60+52wnear25", [r for r in E if r.get("breadth", 0) >= 60
                                  and r.get("dist52w", 99) <= 25]),
            ("E+br60+52wnear10", [r for r in E if r.get("breadth", 0) >= 60
                                  and r.get("dist52w", 99) <= 10]),
            ("E+br60+lowvol",    [r for r in E if r.get("breadth", 0) >= 60
                                  and r.get("vol_pct", 1) <= 0.33]),
            ("E+br60+highvol",   [r for r in E if r.get("breadth", 0) >= 60
                                  and r.get("vol_pct", 0) >= 0.67]),
            ("E+br60+pullback",  [r for r in E if r.get("breadth", 0) >= 60
                                  and r.get("ret5_pct", 1) <= 0.30]),
            ("E+br60+chase",     [r for r in E if r.get("breadth", 0) >= 60
                                  and r.get("ret5_pct", 0) >= 0.70]),
            ("E+br75",           [r for r in E if r.get("breadth", 0) >= 75]),
            ("E+br80",           [r for r in E if r.get("breadth", 0) >= 80]),
            ("E+br70+lowvol",    [r for r in E if r.get("breadth", 0) >= 70
                                  and r.get("vol_pct", 1) <= 0.33]),
            ("E+br70+rising",    [r for r in E if r.get("breadth", 0) >= 70
                                  and (r.get("br_chg5") or 0) > 0]),
        ]
        print(f"\n=== {scope_name} — {len(scope)} records | mkt10 {market10:+.2f}% ===")
        print(f"  {'Variant':22s} {'n':>6s} {'win%':>6s} {'avg%':>7s} {'alpha':>7s} | {'T+20 avg':>8s} {'a20':>7s}")
        sc = {}
        for name, recs in variants:
            st10 = _stats([r["fwd_trail12"] for r in recs], market10)
            f20 = [r["fwd20"] for r in recs if not np.isnan(r.get("fwd20", float("nan")))]
            st20 = _stats(f20, market20)
            sc[name] = {"t10": st10, "t20": st20}
            if st10["n"]:
                a20 = f"{st20.get('alpha', 0):+7.2f}" if st20.get("n") else "      -"
                v20 = f"{st20.get('avg', 0):+8.2f}" if st20.get("n") else "       -"
                print(f"  {name:22s} {st10['n']:>6d} {st10['win']:>6.1f} {st10['avg']:>+7.2f} "
                      f"{st10['alpha']:>+7.2f} | {v20} {a20}")
            else:
                print(f"  {name:22s} {'0':>6s}")
        out["scopes"][scope_name] = sc

    RESULTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nLuu: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
