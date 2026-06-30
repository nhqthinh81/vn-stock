"""Fetch và cache chỉ số cơ bản toàn thị trường từ vnstock KBS source."""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

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

        pat0 = _get("profit_after_tax_for_shareholders_of_the_parent_company", 0)
        pat4 = _get("profit_after_tax_for_shareholders_of_the_parent_company", 4)
        pat_growth = None
        if pat0 is not None and pat4 is not None and pat4 != 0:
            pat_growth = (pat0 - pat4) / abs(pat4) * 100

        return {
            "symbol":       symbol,
            "pe":           pe,
            "pb":           pb,
            "roe":          roe * 100 if roe is not None else None,
            "de":           de,
            "net_margin":   nm * 100 if nm is not None else None,
            "gross_margin": gm * 100 if gm is not None else None,
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
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def scan_all_fundamentals(
    symbols: Optional[list] = None,
    progress_callback: Optional[Callable] = None,
    resume: bool = True,
    delay: float = 0.05,
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
        rec = fetch_fundamental(sym)
        if rec:
            results_map[sym] = rec
        done_set.add(sym)
        if i % 50 == 0:
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
        }, ensure_ascii=False),
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
) -> list[dict]:
    """Lọc danh sách mã theo checklist đầu tư. Trả danh sách đã sắp xếp theo ROE giảm dần."""
    result = []
    for r in rows:
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
