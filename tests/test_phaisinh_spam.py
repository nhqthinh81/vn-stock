"""Kiểm chứng 2 lớp chống spam Telegram.

Chuyển từ scratchpad/test_spam.py (phase 28g) sang pytest chính thức.
"""
import inspect
import time

import requests

import vn_invest.phaisinh_tab as ps


class _Resp:
    ok = True
    status_code = 200

    def json(self):
        return {}


def test_telegram_dedup(monkeypatch):
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    sent = []
    monkeypatch.setenv("TELEGRAM_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (sent.append(k.get("json", {}).get("text", "")), _Resp())[1])
    monkeypatch.setattr(ps, "_TG_LAST", {})

    # 1. Tin nhan y het nhau bi chan
    for _ in range(10):
        ps._send_telegram("🚀 MỞ LONG #1\nVào: 1890,0")
    chk(len(sent) == 1, f"goi 10 lan -> chi gui {len(sent)} (mong 1)")

    # 2. Tin nhan KHAC nhau van gui binh thuong
    sent.clear(); ps._TG_LAST.clear()
    for i in range(5):
        ps._send_telegram(f"🚀 MỞ LONG #{i}\nVào: 189{i},0")
    chk(len(sent) == 5, f"5 tin khac nhau -> gui {len(sent)} (mong 5)")

    # 3. Het thoi han thi gui lai duoc
    sent.clear(); ps._TG_LAST.clear()
    ps._send_telegram("cảnh báo A")
    monkeypatch.setattr(ps, "_TG_DEDUP_SEC", 0)   # gia lap da qua thoi han
    time.sleep(0.01)
    ps._send_telegram("cảnh báo A")
    chk(len(sent) == 2, f"sau khi het han -> gui {len(sent)} (mong 2)")

    assert not fails, "\n" + "\n".join(fails)


def test_per_bar_guard_commits_before_side_effects():
    """Chốt chặn mỗi-nến-một-lần: commit `ps_last_time` PHẢI nằm trước mọi tác
    động phụ (gửi Telegram/ghi journal) — commit muộn từng gây spam ~60
    tin/phút khi exception xảy ra giữa chừng (fragment chạy 1 lần/giây)."""
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    srcf = inspect.getsource(ps._live_panel_body)
    lines = srcf.splitlines()
    i_guard = next(i for i, l in enumerate(lines)
                   if 'if last_time != st.session_state["ps_last_time"]:' in l)
    i_commit = next(i for i, l in enumerate(lines)
                    if 'st.session_state["ps_last_time"] = last_time' in l)
    i_sends = [i for i, l in enumerate(lines) if "_send_telegram_async(" in l]
    i_journ = [i for i, l in enumerate(lines) if "_append_journal(" in l]
    after = [i for i in i_sends + i_journ if i > i_guard]

    chk(i_commit == i_guard + 7 or i_commit < min(after),
        f"commit ({i_commit}) NAM TRUOC moi tac dong phu (som nhat {min(after)})")
    chk(sum(1 for l in lines if 'st.session_state["ps_last_time"] = last_time' in l) == 1,
        "chi con DUNG 1 cho commit (khong con dong cu o cuoi)")

    assert not fails, "\n" + "\n".join(fails)
