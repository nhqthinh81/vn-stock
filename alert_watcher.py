# -*- coding: utf-8 -*-
"""
Alert Watcher — process nền theo dõi scan_result.csv, tự gửi Telegram khi file đổi.

Cách hoạt động:
  - Poll mtime của C:\\AmibrokerData\\scan_result.csv mỗi POLL_SECONDS (mặc định 30s)
  - Khi AmiBroker Explorer ghi file mới → chạy check_and_alert():
      quét + composite score + spam filter (cooldown 3 ngày) + gửi TOP 10 Telegram
  - Log ra console + data/alert_watcher.log

Chạy:
    python alert_watcher.py             # chạy liên tục (Ctrl+C để dừng)
    python alert_watcher.py --once      # kiểm tra 1 lần rồi thoát (để test)

Tự khởi động cùng Windows: dùng file start_alert_watcher.bat (đặt shortcut vào
shell:startup) hoặc Task Scheduler trigger "At log on".
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).parent
sys.path.insert(0, str(APP))
sys.stdout.reconfigure(encoding="utf-8")

POLL_SECONDS = int(os.getenv("ALERT_WATCHER_POLL", "60"))

# ── Logging: console + file ──────────────────────────────────────────────────
LOG_PATH = APP / "data" / "alert_watcher.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("alert_watcher")


def _load_env() -> None:
    """Nạp .env thủ công (watcher chạy ngoài Streamlit nên không có sẵn)."""
    env = APP / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run_once() -> bool:
    """Kiểm tra 1 lần. Trả True nếu có alert được xử lý."""
    from vn_invest.alerter import check_and_alert
    from vn_invest.lstm import model_ready
    _use_lstm = model_ready()
    if not _use_lstm:
        log.warning("Model LSTM chua san sang — chay tam voi AMI 60%%+KT 40%% (khong co AI Score)")
    t0 = time.time()
    result = check_and_alert(use_lstm=_use_lstm)
    if result is None:
        return False
    log.info(
        "scan_result.csv MOI -> scanned=%d qualified=%d sent=%d spam_skip=%d capped=%d (%.0fs, lstm=%s)",
        result.get("scanned", 0), result.get("qualified", 0),
        result.get("sent", 0), result.get("skipped_spam", 0),
        result.get("capped", 0), time.time() - t0, _use_lstm,
    )
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Kiem tra 1 lan roi thoat")
    args = ap.parse_args()

    _load_env()
    if not os.getenv("TELEGRAM_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        log.error("Thieu TELEGRAM_TOKEN / TELEGRAM_CHAT_ID trong .env — dung.")
        sys.exit(1)

    from vn_invest.alerter import _AMI_SCAN
    log.info("Watcher khoi dong — theo doi %s (poll %ds, gui top 10, cooldown 3 ngay)",
             _AMI_SCAN, POLL_SECONDS)

    if args.once:
        changed = run_once()
        log.info("Che do --once: %s", "da xu ly file moi" if changed else "file chua doi")
        return

    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            log.info("Nhan Ctrl+C — dung watcher.")
            break
        except Exception as e:
            log.error("Loi vong lap: %s: %s", type(e).__name__, e)
        try:
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            log.info("Nhan Ctrl+C — dung watcher.")
            break


if __name__ == "__main__":
    main()
