"""
Backtest technical component của Pre-Trade Scoring.

Kiểm tra alpha thực tế của từng nhóm tín hiệu kỹ thuật trên dữ liệu lịch sử VN.
Chỉ backtest được phần KT (tech_score) vì Fund/Macro/News là point-in-time.

Output  : data/pretrade_backtest.json
Progress: data/pretrade_backtest_progress.json

Chạy: python backtest_pretrade.py [--max N]
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))

from vn_invest.screener import load_cache
from vn_invest.data import get_price_history
from vn_invest.indicators import (
    add_all_indicators,
    calculate_tech_score,
    classify_phase,
    classify_signal,
)

_PROGRESS_FILE = APP_DIR / "data" / "pretrade_backtest_progress.json"
_RESULT_FILE   = APP_DIR / "data" / "pretrade_backtest.json"
_MIN_INDICATOR_BARS = 52   # đủ cho SMA50 + warm-up indicator
_FWD_DAYS = [5, 10, 20]
_DELAY    = 0.35           # s giữa các symbol — tránh rate limit vnstock


def _write_progress(done: int, total: int, symbol: str = "", status: str = "running") -> None:
    _PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROGRESS_FILE.write_text(
        json.dumps({
            "status":     status,
            "done":       done,
            "total":      total,
            "current":    symbol,
            "pct":        round(done / max(total, 1) * 100, 1),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _safe(v, d: float = 0.0) -> float:
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except Exception:
        return d


def _tech_score_row(row: dict) -> float:
    """Tính tech_score cho 1 hàng trong DataFrame sau add_all_indicators."""
    rsi  = _safe(row.get("rsi"), 50)
    mh   = _safe(row.get("macd_hist"), 0)
    dist = _safe(row.get("dist_ema34_pct"), 0)
    ma_al = int(_safe(row.get("ma_aligned"), 0))
    volr  = _safe(row.get("volume_ratio"), float("nan"))
    bsc   = int(_safe(row.get("macd_bars_since_cross"), 999))
    wmt   = int(_safe(row.get("weekly_macd_trend"), 0))
    tr20  = _safe(row.get("price_trend_20d"), 0)
    phase = classify_phase(rsi, dist, tr20, ma_al)
    return calculate_tech_score(rsi, mh, dist, ma_al, volr, bsc, phase, wmt)


def _group_stats(rows: list[dict], fd: int) -> dict:
    vals = [r[f"fwd_{fd}"] for r in rows if r.get(f"fwd_{fd}") is not None]
    if not vals:
        return {"n": 0, "avg": None, "win_rate": None, "median": None}
    n    = len(vals)
    avg  = sum(vals) / n
    srt  = sorted(vals)
    med  = srt[n // 2]
    wins = sum(1 for v in vals if v > 0)
    return {
        "n":        n,
        "avg":      round(avg, 2),
        "win_rate": round(wins / n, 3),
        "median":   round(med, 2),
    }


def main(max_symbols: int = 80) -> None:
    _write_progress(0, 0, status="starting")

    cache   = load_cache()
    symbols = [r["symbol"] for r in cache if r.get("symbol") and len(r.get("symbol", "")) <= 10]
    symbols = list(dict.fromkeys(symbols))[:max_symbols]  # dedup + giới hạn

    total = len(symbols)
    _write_progress(0, total)
    print(f"[Backtest Pre-Trade] {total} symbols, est ~{total * _DELAY / 60:.0f} min...", flush=True)

    all_rows: list[dict] = []
    failed = 0

    for i, sym in enumerate(symbols):
        _write_progress(i, total, sym)
        try:
            df = get_price_history(sym, days=365)
            need_rows = _MIN_INDICATOR_BARS + max(_FWD_DAYS)
            if df is None or len(df) < need_rows:
                failed += 1
                continue

            df = add_all_indicators(df)
            df = df.dropna(subset=["rsi", "macd_hist", "dist_ema34_pct"]).reset_index(drop=True)
            close_arr = df["close"].values

            for idx in range(_MIN_INDICATOR_BARS, len(df)):
                row_d = df.iloc[idx].to_dict()
                ts    = _tech_score_row(row_d)
                volr  = _safe(row_d.get("volume_ratio"), float("nan"))
                rsi   = _safe(row_d.get("rsi"), 50)
                sig   = classify_signal(ts, volr, rsi)

                entry: dict = {"symbol": sym, "tech_score": round(ts, 1), "signal": sig}
                c0 = close_arr[idx]
                for fd in _FWD_DAYS:
                    j = idx + fd
                    entry[f"fwd_{fd}"] = (
                        round((close_arr[j] / c0 - 1) * 100, 3)
                        if j < len(close_arr) and c0 > 0 else None
                    )
                all_rows.append(entry)

        except Exception as e:
            failed += 1
            print(f"  [{sym}] lỗi: {e}", flush=True)
            time.sleep(0.5)

        time.sleep(_DELAY)

    _write_progress(total, total, status="aggregating")
    print(f"[Backtest] {len(all_rows):,} data points from {total - failed}/{total} symbols", flush=True)

    # ── Aggregate theo signal ─────────────────────────────────────────────────
    _SIGNALS = ["BUY-A*", "BUY-A", "BUY-B", "HOLD", "SELL-B", "SELL-A"]
    by_signal: dict = {}
    for sig in _SIGNALS:
        sig_rows = [r for r in all_rows if r["signal"] == sig]
        if sig_rows:
            by_signal[sig] = {f"t{fd}": _group_stats(sig_rows, fd) for fd in _FWD_DAYS}
            by_signal[sig]["n_total"] = len(sig_rows)

    # ── Aggregate theo score bucket (ánh xạ sang MUA/CHỜ/TRÁNH zone) ─────────
    _BUCKETS = [
        ("MUA zone (≥70)",    70, 101),
        ("CHỜ zone (50-70)",  50,  70),
        ("TRÁNH zone (<50)",   0,  50),
    ]
    by_score: dict = {}
    for label, lo, hi in _BUCKETS:
        bkt = [r for r in all_rows if lo <= r["tech_score"] < hi]
        if bkt:
            by_score[label] = {f"t{fd}": _group_stats(bkt, fd) for fd in _FWD_DAYS}
            by_score[label]["n_total"] = len(bkt)

    # ── Alpha của BUY-A/BUY-A* vs baseline (HOLD) ────────────────────────────
    def _alpha(sig_key: str, fd: int) -> str | None:
        if sig_key not in by_signal or "HOLD" not in by_signal:
            return None
        a = by_signal[sig_key][f"t{fd}"]["avg"]
        h = by_signal["HOLD"][f"t{fd}"]["avg"]
        if a is None or h is None:
            return None
        return f"{a - h:+.2f}%"

    verdict_parts = []
    for sig in ["BUY-A*", "BUY-A"]:
        if sig in by_signal:
            t10 = by_signal[sig]["t10"]
            if t10.get("avg") is not None:
                verdict_parts.append(
                    f"{sig} avg T+10={t10['avg']:+.2f}% | win={t10['win_rate']:.0%}"
                    + (f" | alpha={_alpha(sig, 10)}" if _alpha(sig, 10) else "")
                )

    result = {
        "run_at":        datetime.now().strftime("%Y-%m-%d %H:%M"),
        "symbols_count": total - failed,
        "data_points":   len(all_rows),
        "by_signal":     by_signal,
        "by_score":      by_score,
        "verdict":       " · ".join(verdict_parts) if verdict_parts else "Chưa đủ dữ liệu",
        "note": (
            "Backtest chỉ kiểm chứng phần KT (tech_score). "
            "Fund/Macro/News là point-in-time, không tái tạo được cho dữ liệu lịch sử."
        ),
    }

    _RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _RESULT_FILE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_progress(total, total, status="done")
    print(f"[Backtest] Done -> {_RESULT_FILE}", flush=True)
    print(f"[Backtest] Verdict: {result['verdict']}", flush=True)


if __name__ == "__main__":
    _max = 80
    for _a in sys.argv[1:]:
        if _a.startswith("--max="):
            try: _max = int(_a.split("=")[1])
            except ValueError: pass
    main(max_symbols=_max)
