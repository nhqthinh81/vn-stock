"""Script độc lập: quét chỉ số cơ bản toàn thị trường, ghi tiến độ ra file JSON.
Chạy: python update_fundamental.py
Hỗ trợ resume từ checkpoint nếu bị ngắt giữa chừng.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

_DATA_DIR    = Path(__file__).parent / "data"
_PROG_PATH   = _DATA_DIR / "fundamental_progress.json"


def _write_progress(done: int, total: int, current: str, status: str = "running") -> None:
    _PROG_PATH.write_text(
        json.dumps({
            "status":  status,
            "done":    done,
            "total":   total,
            "current": current,
            "pct":     round(done / max(total, 1) * 100, 1),
            "ts":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    _write_progress(0, 1, "Đang lấy danh sách mã...", "running")

    from vn_invest.fundamental_scanner import scan_all_fundamentals

    # Lấy tổng số mã trước để hiển thị progress đúng
    from vnstock import Listing
    symbols = Listing().all_symbols()["symbol"].tolist()
    total = len(symbols)
    _write_progress(0, total, "Bắt đầu quét...", "running")
    print(f"Bắt đầu quét {total} mã...", flush=True)

    def _cb(i: int, t: int, sym: str) -> None:
        _write_progress(i, t, sym, "running")
        if i % 50 == 0:
            print(f"  {i}/{t} — {sym}", flush=True)

    scan_all_fundamentals(symbols=symbols, progress_callback=_cb, resume=True)

    _write_progress(total, total, "Hoàn tất", "done")
    print(f"Hoàn tất! Đã quét {total} mã.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _write_progress(0, 0, "Bị ngắt bởi người dùng", "interrupted")
        sys.exit(0)
    except Exception as e:
        _PROG_PATH.write_text(
            json.dumps({"status": "error", "error": str(e), "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        raise
