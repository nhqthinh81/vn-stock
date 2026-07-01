"""Fetch và cache chỉ số cơ bản toàn thị trường từ vnstock KBS source."""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

_FETCH_TIMEOUT = 15  # giây tối đa mỗi mã (requests timeout thực sự)

_DATA_DIR   = Path(__file__).parent.parent / "data"
_CACHE_PATH = _DATA_DIR / "fundamental_cache.json"
_CKPT_PATH  = _DATA_DIR / "fundamental_cache.checkpoint.json"


def fetch_fundamental(symbol: str) -> Optional[dict]:
    """Fetch chỉ số cơ bản 1 mã từ KBS. Trả None nếu lỗi hoặc không có data."""
    try:
        from vnstock import Finance
        r = Finance(symbol=symbol, source="KBS").ratio()
        if r is None or r.empty:
            return None
        meta_cols = {"item", "item_id", "item_en"}
        year_cols = [c for c in r.columns if c not in meta_cols]
        if not year_cols:
            return None

        def _get(item_id: str, col_idx: int = 0):
            mask = r["item_id"] == item_id
            if not mask.any() or col_idx >= len(year_cols):
                return None
            try:
                val = r[mask][year_cols[col_idx]].values[0]
                return float(val) if val is not None else None
            except Exception:
                return None

        pe  = _get("pe_ratio")
        pb  = _get("pb_ratio")
        roe = _get("roe")
        de  = _get("debt_to_equity")
        nm  = _get("net_margin")
        gm  = _get("gross_margin")
        ci  = _get("cash_to_income")

        # KBS trả % tăng trưởng YoY cho PAT trực tiếp ở col[0]
        pat_growth = _get("profit_after_tax_for_shareholders_of_the_parent_company", 0)

        return {
            "symbol":       symbol,
            "pe":           pe,
            "pb":           pb,
            # KBS trả ROE theo quý (%), nhân 4 để annualize
            "roe":          roe * 4 if roe is not None else None,
            # KBS trả D/E dạng % (70.24 = 0.7024x), chia 100 để ra ratio
            "de":           de / 100 if de is not None else None,
            # net_margin, gross_margin đã là % — không nhân 100
            "net_margin":   nm,
            "gross_margin": gm,
            "cash_quality": ci,
            "pat_growth":   pat_growth,
            "period":       year_cols[0] if year_cols else None,
        }
    except Exception:
        return None


def _load_checkpoint() -> dict:
    if _CKPT_PATH.exists():
        try:
            return json.loads(_CKPT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_checkpoint(done: list, all_syms: list, partial_map: dict) -> None:
    _CKPT_PATH.write_text(
        json.dumps({
            "done":        done,
            "all_symbols": all_syms,
            "partial":     list(partial_map.values()),
            "saved_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=True),
        encoding="utf-8",
    )


def fetch_avg_volume(symbol: str) -> Optional[float]:
    """Fetch khối lượng giao dịch TB 20 phiên gần nhất (VCI source)."""
    try:
        from datetime import timedelta
        from vnstock import Vnstock
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
        h = Vnstock().stock(symbol=symbol, source="VCI").quote.history(
            start=start, end=end, interval="1D"
        )
        if h is None or h.empty or "volume" not in h.columns:
            return None
        tail = h["volume"].tail(20)
        return float(tail.mean()) if len(tail) > 0 else None
    except Exception:
        return None


def enrich_with_volumes(
    results: list[dict],
    progress_callback: Optional[Callable] = None,
    delay: float = 0.5,
) -> list[dict]:
    """Bổ sung avg_vol_20d vào từng record và ghi lại cache."""
    total = len(results)
    for i, rec in enumerate(results):
        if progress_callback:
            progress_callback(i, total, rec["symbol"])
        if rec.get("avg_vol_20d") is not None:
            continue  # đã có rồi, bỏ qua
        try:
            vol = fetch_avg_volume(rec["symbol"])
        except Exception:
            vol = None
        rec["avg_vol_20d"] = vol
        time.sleep(delay)
    _CACHE_PATH.write_text(
        json.dumps({
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count":      len(results),
            "data":       results,
        }, ensure_ascii=True),
        encoding="utf-8",
    )
    return results


def scan_all_fundamentals(
    symbols: Optional[list] = None,
    progress_callback: Optional[Callable] = None,
    resume: bool = True,
    delay: float = 0.3,
) -> list[dict]:
    """Quét toàn thị trường. Hỗ trợ resume từ checkpoint nếu bị ngắt giữa chừng."""
    if symbols is None:
        from vnstock import Listing
        symbols = Listing().all_symbols()["symbol"].tolist()

    results_map: dict[str, dict] = {}
    done_set: set[str] = set()

    if resume:
        ckpt = _load_checkpoint()
        if ckpt.get("all_symbols") == symbols:
            done_set = set(ckpt.get("done", []))
            for rec in ckpt.get("partial", []):
                results_map[rec["symbol"]] = rec

    total = len(symbols)
    for i, sym in enumerate(symbols):
        if progress_callback:
            progress_callback(i, total, sym)
        if sym in done_set:
            continue
        try:
            rec = fetch_fundamental(sym)
        except Exception as _e:
            # RateLimitExceeded từ vnai (sau khi patch CleanErrorContext)
            # → sleep retry_after rồi thử lại 1 lần
            _ename = type(_e).__name__
            if "RateLimit" in _ename or "rate" in _ename.lower():
                _wait = min(int(getattr(_e, "retry_after", 65) or 65) + 5, 120)
                time.sleep(_wait)
                try:
                    rec = fetch_fundamental(sym)
                except Exception:
                    rec = None
            else:
                rec = None
        if rec:
            results_map[sym] = rec
        done_set.add(sym)
        if i % 20 == 0:
            _save_checkpoint(list(done_set), symbols, results_map)
        time.sleep(delay)

    if progress_callback:
        progress_callback(total, total, "Done")

    results = list(results_map.values())
    _CACHE_PATH.write_text(
        json.dumps({
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count":      len(results),
            "data":       results,
        }, ensure_ascii=True),
        encoding="utf-8",
    )
    _CKPT_PATH.unlink(missing_ok=True)
    return results


def load_fundamental_cache() -> tuple[list[dict], str]:
    """Trả về (danh_sách_mã, ngày_cập_nhật)."""
    if not _CACHE_PATH.exists():
        return [], ""
    try:
        d = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return d.get("data", []), d.get("updated_at", "")
    except Exception:
        return [], ""


def load_checkpoint_meta() -> dict:
    """Trả metadata checkpoint đang chạy dở (để hiển thị tiến độ khi restart)."""
    ckpt = _load_checkpoint()
    if not ckpt:
        return {}
    done = ckpt.get("done", [])
    total = len(ckpt.get("all_symbols", []))
    return {"done": len(done), "total": total, "saved_at": ckpt.get("saved_at", "")}


def filter_checklist(
    rows: list[dict],
    pe_max: float = 25.0,
    pb_max: float = 3.0,
    roe_min: float = 15.0,
    de_max: float = 1.5,
    net_margin_min: float = 0.0,
    min_pass: int = 4,
    vol_min: float = 0.0,
) -> list[dict]:
    """Lọc danh sách mã theo checklist đầu tư. Trả danh sách đã sắp xếp theo ROE giảm dần."""
    result = []
    for r in rows:
        # Hard filter thanh khoản — áp dụng trước checklist
        if vol_min > 0:
            avg_vol = r.get("avg_vol_20d")
            if avg_vol is None or avg_vol < vol_min:
                continue
        checks = {
            "P/E hợp lý":     (r.get("pe")  is not None) and r["pe"]  < pe_max,
            "P/B hợp lý":     (r.get("pb")  is not None) and r["pb"]  < pb_max,
            "ROE > {:g}%".format(roe_min):
                              (r.get("roe") is not None) and r["roe"] > roe_min,
            "Nợ/VCSH < {:g}x".format(de_max):
                              (r.get("de")  is not None) and r["de"]  < de_max,
            "Biên LN ròng > {:g}%".format(net_margin_min):
                              (r.get("net_margin") is not None) and r["net_margin"] > net_margin_min,
        }
        n_pass = sum(1 for v in checks.values() if v)
        if n_pass >= min_pass:
            rec = dict(r)
            rec["n_pass"] = n_pass
            rec["checks"] = checks
            result.append(rec)
    return sorted(result, key=lambda x: x.get("roe") or 0, reverse=True)
