# -*- coding: utf-8 -*-
"""Cảnh báo khi bot NGỪNG CHẠY — hai cái chết im lặng của ngày 10/09/2026.

Ngày đó mất trắng cả phiên vì hai thứ hỏng mà không phát ra tiếng động nào:

  06:21 → 11:25  AmiBroker treo, feed 1 phút đứng nguyên (mất phiên sáng)
  10:50 → 13:58  không trình duyệt nào mở tab Phái Sinh nên engine không quay
                 (mất phần lớn phiên chiều)

Cả hai đều KHÔNG thể tự báo động, vì lý do đối xứng nhau:

  - Engine phái sinh sống trong `@st.fragment(run_every=1)`, chỉ quay khi có
    trình duyệt đang mở tab. Nó chết thì cũng không còn ai để gửi cảnh báo.
  - `autotrade_runtime` worker là daemon thread: nó vẫn tick đều sau khi engine
    chết, nên "worker còn sống" KHÔNG chứng minh được "bot đang chạy". Đây là
    cái bẫy đã làm mất 3 tiếng để nhận ra.

Vì vậy bộ canh này BẮT BUỘC phải nằm ngoài tiến trình Streamlit. Nó sống trong
`alert_watcher.py` — tiến trình nền độc lập, tự khởi động cùng Windows.

Module cố ý KHÔNG import `phaisinh_tab` (kéo theo streamlit) — cùng lý do
`tg_escape`/`fmt_vn` đặt ở `alerter.py`. Cái giá là lặp lại việc dò đường dẫn
AmibrokerData và giờ giao dịch; đổi lại watcher chạy được headless.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, time as dtime
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent

# Engine ghi nhịp tim mỗi ~10s (tiết chế trong _save_ps_state).
# 3 phút = 18 nhịp lỡ liên tiếp — đủ chắc là đã chết, không phải giật lag.
_HEARTBEAT_STALE_MIN = 3.0

# AFL export ~61s/lần (đo thật 10/09). 6 phút = 6 lượt lỡ.
# Cao hơn `_MAX_STALE_MIN = 5` của engine một chút, để engine tự chặn giao dịch
# trước rồi mới tới lượt cảnh báo — tránh báo động khi chỉ trễ nhất thời.
_FEED_STALE_MIN = 6.0

# Nhắc lại trong lúc vẫn hỏng. Không im hẳn sau lần đầu (user có thể lỡ),
# cũng không lặp mỗi phút.
_REPEAT_MIN = 30.0

_STATE_FILE = _APP / "data" / "watchdog_state.json"

_SESSIONS = ((dtime(9, 0), dtime(11, 30)), (dtime(13, 0), dtime(14, 45)))

# Cùng thứ tự ưu tiên với `phaisinh_tab._POTENTIAL_PATHS`.
_POTENTIAL_DATA_DIRS = (r"D:\AmibrokerData", r"C:\AmibrokerData",
                        os.path.join(os.getcwd(), "AmibrokerData"))


def _feed_path() -> Path | None:
    for d in _POTENTIAL_DATA_DIRS:
        p = Path(d) / "vn30f1m_1min.csv"
        if p.exists():
            return p
    return None


def session_segment(now: datetime):
    """Trả (đầu, cuối) của phiên đang mở, hoặc None nếu ngoài giờ/cuối tuần."""
    if now.weekday() >= 5:
        return None
    for start, end in _SESSIONS:
        if start <= now.time() < end:
            return datetime.combine(now.date(), start), datetime.combine(now.date(), end)
    return None


def _heartbeat_age_min(now: datetime) -> float | None:
    """Phút kể từ nhịp tim cuối của engine. None nếu chưa từng có nhịp nào."""
    try:
        raw = json.loads((_APP / "data" / "ps_state.json").read_text(encoding="utf-8"))
        beat = (raw.get("owner") or {}).get("heartbeat")
    except (OSError, ValueError, AttributeError):
        return None
    if not beat:
        return None
    try:
        return (now - datetime.fromisoformat(beat)).total_seconds() / 60
    except ValueError:
        return None


def _feed_age_min(now: datetime) -> float | None:
    """Phút kể từ NẾN CUỐI trong feed — đo dữ liệu, không đo mtime.

    Đo bằng mtime là sai: AFL vẫn ghi đè file đều đặn ngay cả khi nguồn intraday
    của AmiBroker đã chết, cho ra file "mới 40 giây" mà nến bên trong là hôm qua.
    """
    path = _feed_path()
    if path is None:
        return None
    try:
        last = ""
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    last = line.strip()
    except OSError:
        return None
    parts = last.split(",")
    if len(parts) < 2:
        return None
    try:
        bar = datetime.strptime(f"{parts[0]} {parts[1]}", "%d/%m/%Y %H:%M")
    except ValueError:
        return None
    return (now - bar).total_seconds() / 60


def check(now: datetime | None = None) -> dict[str, str]:
    """Trả {mã lỗi: mô tả} cho những thứ đang chết. Rỗng = mọi thứ ổn.

    Chỉ xét trong giờ giao dịch: ngoài phiên thì engine đứng im và feed không có
    nến mới là chuyện bình thường, báo động lúc đó chỉ là nhiễu.
    """
    now = now or datetime.now()
    segment = session_segment(now)
    if segment is None:
        return {}
    started_min = (now - segment[0]).total_seconds() / 60

    problems: dict[str, str] = {}

    beat = _heartbeat_age_min(now)
    if beat is None or beat > _HEARTBEAT_STALE_MIN:
        # Đầu phiên cần ân hạn: engine có thể chưa kịp ghi nhịp đầu tiên.
        if started_min >= _HEARTBEAT_STALE_MIN:
            problems["engine"] = (
                f"Engine phái sinh KHÔNG chạy — nhịp tim "
                + (f"đã cũ {beat:.0f} phút" if beat is not None else "chưa từng ghi")
                + ". Không trình duyệt nào mở tab Phái Sinh, hoặc tab đã bị đóng. "
                  "Mở lại http://localhost:8501 và giữ nguyên cửa sổ."
            )

    feed = _feed_age_min(now)
    if feed is None or feed > _FEED_STALE_MIN:
        # Sau nghỉ trưa, nến cuối là của phiên trước nên luôn "cũ" vài chục phút
        # trong ít phút đầu — chỉ xét khi phiên đã chạy đủ lâu.
        if started_min >= _FEED_STALE_MIN:
            problems["feed"] = (
                f"Feed 1 phút đứng — nến cuối "
                + (f"cũ {feed:.0f} phút" if feed is not None else "không đọc được")
                + ". AmiBroker treo hoặc Explore đã ngừng lặp. "
                  "Kiểm tra AmiBroker và chạy lại Explore trên VN30F1M khung 1 phút."
            )
    return problems


def _load_state() -> dict:
    try:
        raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _STATE_FILE)
    except OSError:
        pass          # mất trạng thái chỉ làm cảnh báo lặp lại, không được làm sập watcher


def run(send=None, now: datetime | None = None) -> list[str]:
    """Kiểm tra + gửi Telegram. Trả danh sách tin ĐÃ gửi (để test/log).

    Ba loại tin, mỗi loại có lý do riêng để tồn tại:
      - lần đầu phát hiện chết  → biết ngay mà chữa
      - nhắc lại mỗi 30 phút    → phòng trường hợp lỡ tin đầu
      - báo hồi phục            → biết chắc đã chạy lại, không phải đoán
    """
    now = now or datetime.now()
    if send is None:
        from .alerter import send_telegram as send
    from .alerter import tg_escape

    problems = check(now)
    state = _load_state()
    sent: list[str] = []

    for kind in ("engine", "feed"):
        entry = state.get(kind) or {}
        if kind in problems:
            last = entry.get("last_sent")
            due = True
            if last:
                try:
                    due = (now - datetime.fromisoformat(last)).total_seconds() / 60 >= _REPEAT_MIN
                except ValueError:
                    due = True
            if due:
                again = " (nhắc lại)" if entry.get("dead_since") else ""
                msg = (f"🚨 <b>#VN30F1M BOT KHÔNG CHẠY{tg_escape(again)}</b>\n"
                       f"{tg_escape(problems[kind])}\n"
                       f"🕐 {now:%H:%M:%S %d/%m}")
                if send(msg):
                    sent.append(msg)
                    entry["last_sent"] = now.isoformat()
            entry.setdefault("dead_since", now.isoformat())
            state[kind] = entry
        elif entry.get("dead_since"):
            # Chỉ báo hồi phục nếu trước đó ĐÃ thực sự gửi cảnh báo chết.
            if entry.get("last_sent"):
                down = ""
                try:
                    # Tính từ lúc PHÁT HIỆN, không phải lúc thật sự chết — bộ canh
                    # chỉ chạy mỗi 60s nên không biết thời điểm chết chính xác.
                    # Nói rõ ra để không ai đọc nhầm thành tổng thời gian chết.
                    mins = (now - datetime.fromisoformat(entry["dead_since"])).total_seconds() / 60
                    down = f" sau {mins:.0f} phút (tính từ lúc phát hiện)"
                except ValueError:
                    pass
                label = "Engine phái sinh" if kind == "engine" else "Feed 1 phút"
                msg = (f"✅ <b>#VN30F1M {tg_escape(label)} đã chạy lại</b>"
                       f"{tg_escape(down)}\n🕐 {now:%H:%M:%S %d/%m}")
                if send(msg):
                    sent.append(msg)
            state[kind] = {}

    _save_state(state)
    return sent
