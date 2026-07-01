"""Script doc lap: quet chi so co ban toan thi truong, ghi tien do ra file JSON."""
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

_DATA_DIR  = Path(__file__).parent / "data"
_PROG_PATH = _DATA_DIR / "fundamental_progress.json"
_LOG_PATH  = _DATA_DIR / "fundamental_scan.log"


_MY_PID = __import__("os").getpid()


def _write_progress(done: int, total: int, current: str, status: str = "running") -> None:
    try:
        _PROG_PATH.write_text(
            json.dumps({
                "status":  status,
                "done":    done,
                "total":   total,
                "current": current,
                "pct":     round(done / max(total, 1) * 100, 1),
                "ts":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pid":     _MY_PID,  # UI dung de kiem tra process con song khong
            }, ensure_ascii=True),
            encoding="utf-8",
        )
    except Exception:
        pass  # Progress write that bai khong duoc lam crash scan


def _log(msg: str) -> None:
    try:
        with open(_LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _patch_requests_timeout(timeout: float) -> None:
    """Monkey-patch requests.Session.request de moi HTTP call co timeout thuc su."""
    try:
        import requests as _req
        _orig = _req.Session.request
        def _timed(self, method, url, **kw):
            kw.setdefault("timeout", timeout)
            return _orig(self, method, url, **kw)
        _req.Session.request = _timed
        _log(f"requests.Session patched with timeout={timeout}s")
    except Exception as e:
        _log(f"Warning: could not patch requests: {repr(e)}")


def _patch_vnai_rate_limit() -> None:
    """Patch vnai CleanErrorContext de khong goi sys.exit() khi rate limit.

    vnai goi sys.exit() trong __exit__ khi RateLimitExceeded -> SystemExit bypass
    except Exception -> crash subprocess. Patch nay de RateLimitExceeded propagate
    binh thuong, scan loop se catch va sleep/retry.
    """
    try:
        from vnai.beam.quota import CleanErrorContext
        def _safe_exit(self, exc_type, exc_val, exc_tb):
            return False  # cho exception propagate binh thuong, khong sys.exit()
        CleanErrorContext.__exit__ = _safe_exit
        _log("vnai CleanErrorContext patched (RateLimitExceeded will propagate, not sys.exit)")
    except Exception as e:
        _log(f"Warning: could not patch vnai: {repr(e)}")


def main() -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    _LOG_PATH.write_text("", encoding="utf-8")  # reset log moi lan chay
    _log("Script started")
    _write_progress(0, 1, "Starting...", "running")
    _patch_requests_timeout(15)
    _patch_vnai_rate_limit()

    _log("Importing scan_all_fundamentals...")
    from vn_invest.fundamental_scanner import scan_all_fundamentals
    _log("Importing Listing...")

    from vnstock import Listing
    _log("Fetching symbol list...")
    symbols = Listing().all_symbols()["symbol"].tolist()
    total = len(symbols)
    _log(f"Got {total} symbols")
    _write_progress(0, total, "Starting scan...", "running")

    def _cb(i: int, t: int, sym: str) -> None:
        _write_progress(i, t, sym, "running")
        if i % 50 == 0:
            _log(f"{i}/{t} {sym}")

    _log("Starting scan_all_fundamentals...")
    scan_all_fundamentals(symbols=symbols, progress_callback=_cb, resume=True)
    _log("Fundamental scan complete!")

    # Bổ sung khối lượng giao dịch TB 20 phiên
    _log("Starting volume enrichment...")
    from vn_invest.fundamental_scanner import enrich_with_volumes, load_fundamental_cache
    rows, _ = load_fundamental_cache()
    vol_total = len(rows)
    _write_progress(0, vol_total, "Dang cap nhat khoi luong...", "enriching")

    def _vol_cb(i: int, t: int, sym: str) -> None:
        _write_progress(i, t, f"[VOL] {sym}", "enriching")
        if i % 20 == 0:
            _log(f"[VOL] {i}/{t} {sym}")

    enrich_with_volumes(rows, progress_callback=_vol_cb, delay=0.5)
    _log("Volume enrichment complete!")

    _write_progress(total, total, "Done", "done")
    _log("All done!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _write_progress(0, 0, "Interrupted", "interrupted")
        _log("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        tb = traceback.format_exc()
        _log(f"ERROR: {tb}")
        try:
            _PROG_PATH.write_text(
                json.dumps({
                    "status": "error",
                    "error":  repr(e),   # repr() luon tra ASCII-safe string
                    "traceback": tb,
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, ensure_ascii=True),
                encoding="utf-8",
            )
        except Exception:
            pass
        sys.exit(1)
