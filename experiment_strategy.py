# -*- coding: utf-8 -*-
"""
Thí nghiệm cải tiến chiến thuật BUY-A — chạy 1 lượt backtest, so 6 biến thể:

  A  hold T+10, không filter giá        (baseline, giống backtest cũ)
  B  hold T+10 + giá vào >= 10          (penny filter)
  C  trail 12%,  không filter giá       (exit giống paper trading mới)
  D  trail 12% + giá >= 10
  E  trail 12% + giá >= 10 + chỉ vào khi regime == bull (chặt hơn "không bear")
  F  hold T+10 + giá >= 10 + bull-only

Dùng vn_invest.backtester.backtest_symbol (data AMI local, không gọi API).
Kết quả: data/experiment_strategy.json + in console.

Chạy: python experiment_strategy.py [--max N]
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

from vn_invest.backtester import backtest_symbol, _get_ami_dir
from vn_invest.market_regime import get_regime_series, get_vni_return_series

RESULTS_PATH = Path(__file__).parent / "data" / "experiment_strategy.json"
FORWARD_DAYS = 10
MIN_PRICE    = 10.0

CONFIGS = {
    "A_hold_baseline":        {"exit": "fwd_hold",    "min_price": None,      "bull_only": False},
    "B_hold_minprice":        {"exit": "fwd_hold",    "min_price": MIN_PRICE, "bull_only": False},
    "C_trail12":              {"exit": "fwd_trail12", "min_price": None,      "bull_only": False},
    "D_trail12_minprice":     {"exit": "fwd_trail12", "min_price": MIN_PRICE, "bull_only": False},
    "E_trail12_minprice_bull":{"exit": "fwd_trail12", "min_price": MIN_PRICE, "bull_only": True},
    "F_hold_minprice_bull":   {"exit": "fwd_hold",    "min_price": MIN_PRICE, "bull_only": True},
    "G_trail12_bull":         {"exit": "fwd_trail12", "min_price": None,      "bull_only": True},
    "H_hold_bull":            {"exit": "fwd_hold",    "min_price": None,      "bull_only": True},
    # Trendline variants trên nền config E (trail12+minprice+bull):
    "I_E_tl_veto_breaksup":   {"exit": "fwd_trail12", "min_price": MIN_PRICE, "bull_only": True,
                               "tl_exclude": ["break_sup"]},
    "K_E_tl_require_signal":  {"exit": "fwd_trail12", "min_price": MIN_PRICE, "bull_only": True,
                               "tl_require": ["test_sup", "brk_res"]},
}


def _filter_records(records: list[dict], cfg: dict) -> list[dict]:
    out = records
    if cfg["min_price"] is not None:
        out = [r for r in out if r["entry_close"] >= cfg["min_price"]]
    if cfg["bull_only"]:
        # bull-only chỉ áp cho tín hiệu BUY; các signal khác giữ nguyên làm baseline market
        out = [r for r in out
               if not r["signal"].startswith("BUY") or r.get("regime") == "bull"]
    # Trendline filter: chỉ áp cho tín hiệu BUY
    if cfg.get("tl_exclude"):
        out = [r for r in out
               if not r["signal"].startswith("BUY") or r.get("tl") not in cfg["tl_exclude"]]
    if cfg.get("tl_require"):
        out = [r for r in out
               if not r["signal"].startswith("BUY") or r.get("tl") in cfg["tl_require"]]
    return out


def _stats(records: list[dict], cfg: dict) -> dict:
    ret_key = cfg["exit"]
    result = {}
    all_rets = [r[ret_key] for r in records]
    market_avg = float(np.mean(all_rets)) if all_rets else None

    for sig in ("BUY-A", "BUY-B"):
        rets = [r[ret_key] for r in records if r["signal"] == sig]
        if not rets:
            result[sig] = {"n": 0}
            continue
        wins = sum(1 for x in rets if x > 0)
        result[sig] = {
            "n":      len(rets),
            "win":    round(wins / len(rets) * 100, 1),
            "avg":    round(float(np.mean(rets)), 2),
            "median": round(float(np.median(rets)), 2),
            "std":    round(float(np.std(rets)), 2),
            "alpha":  round(float(np.mean(rets)) - market_avg, 2) if market_avg is not None else None,
        }
    result["market_avg"] = round(market_avg, 2) if market_avg is not None else None
    result["n_total"]    = len(records)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=200, help="So ma toi da")
    args = ap.parse_args()

    ami_dir = _get_ami_dir()
    symbols = sorted(p.stem.upper() for p in ami_dir.glob("*.csv")
                     if p.stem.upper() != "VNI")[:args.max]
    print(f"Thi nghiem tren {len(symbols)} ma | forward T+{FORWARD_DAYS} | AMI: {ami_dir}")

    regime_series  = get_regime_series(ami_dir)
    vni_ret_series = get_vni_return_series(14, ami_dir)
    print(f"Regime series: {len(regime_series)} ngay | VNI ret series: {len(vni_ret_series)}")

    t0 = time.time()
    all_records: list[dict] = []
    for i, sym in enumerate(symbols, 1):
        recs = backtest_symbol(
            sym, forward_days=FORWARD_DAYS,
            regime_series=regime_series,
            vni_ret_series=vni_ret_series,
        )
        all_records.extend(recs)
        if i % 25 == 0 or i == len(symbols):
            print(f"  [{i}/{len(symbols)}] {sym} — {len(all_records)} records ({time.time()-t0:.0f}s)")

    # 2 phạm vi: full history (regime cũ toàn "neutral" giả → E/F chỉ tham khảo)
    # và giai đoạn có regime data thật (vni_cache ~1 năm gần nhất)
    regime_start = str(regime_series.index.min())[:10] if not regime_series.empty else "2025-03-01"
    recent = [r for r in all_records if r.get("date", "") >= regime_start]

    out = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "symbols": len(symbols), "forward_days": FORWARD_DAYS,
           "regime_start": regime_start,
           "n_full": len(all_records), "n_recent": len(recent),
           "scopes": {}}

    for scope_name, scope_recs in [("full_history", all_records),
                                   (f"recent(>={regime_start})", recent)]:
        print(f"\n=== {scope_name} — {len(scope_recs)} records ===")
        hdr = f"{'Config':26s} {'n(A)':>6s} {'win%':>6s} {'avg%':>7s} {'med%':>7s} {'alpha':>7s} {'mkt%':>6s}"
        print(hdr); print("-" * len(hdr))
        scope_out = {}
        for name, cfg in CONFIGS.items():
            recs = _filter_records(scope_recs, cfg)
            st = _stats(recs, cfg)
            scope_out[name] = st
            a = st.get("BUY-A", {})
            if a.get("n"):
                print(f"{name:26s} {a['n']:>6d} {a['win']:>6.1f} {a['avg']:>+7.2f} "
                      f"{a['median']:>+7.2f} {a['alpha']:>+7.2f} {st['market_avg']:>+6.2f}")
            else:
                print(f"{name:26s} {'0':>6s}  -")
        out["scopes"][scope_name] = scope_out

        # ── Phân rã theo trendline state (fwd_hold, mẫu lớn) ──
        print(f"\n  Trendline breakdown ({scope_name}):")
        print(f"  {'Nhom':22s} {'tl_state':10s} {'n':>6s} {'win%':>6s} {'avg%':>7s} {'med%':>7s}")
        tl_out = {}
        for grp_name, grp in [("ALL", scope_recs),
                              ("BUY-A/B", [r for r in scope_recs if r["signal"].startswith("BUY")])]:
            for tl in ("none", "test_sup", "brk_res", "break_sup"):
                rets = [r["fwd_hold"] for r in grp if r.get("tl") == tl]
                if not rets:
                    continue
                wins = sum(1 for x in rets if x > 0)
                row = {"n": len(rets), "win": round(wins/len(rets)*100, 1),
                       "avg": round(float(np.mean(rets)), 2),
                       "median": round(float(np.median(rets)), 2)}
                tl_out[f"{grp_name}|{tl}"] = row
                print(f"  {grp_name:22s} {tl:10s} {row['n']:>6d} {row['win']:>6.1f} "
                      f"{row['avg']:>+7.2f} {row['median']:>+7.2f}")
        out["scopes"][scope_name + "_tl_breakdown"] = tl_out

    RESULTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nLuu: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
