"""Script doc lap: quet chi so co ban toan thi truong, ghi tien do ra file JSON."""
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

_DATA_DIR  = Path(__file__).parent / "data"
_PROG_PATH = _DATA_DIR / "fundamental_progress.json"
_LOG_PATH  = _DATA_DIR / "fundamental_scan.log"


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
            }, ensure_ascii=True),   # ensure_ascii=True: tuyet doi an toan, khong phu thuoc encoding
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


def main() -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    _LOG_PATH.write_text("", encoding="utf-8")  # reset log moi lan chay
    _log("Script started")
    _write_progress(0, 1, "Starting...", "running")

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

    _write_progress(total, total, "Done", "done")
    _log("Scan complete!")


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
