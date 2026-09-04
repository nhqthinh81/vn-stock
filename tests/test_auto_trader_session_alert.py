"""Kiểm chứng cảnh báo Telegram khi submit_signal() gặp phiên VPS đã chết.

Chuyển từ scratchpad/test_session_alert.py (phase 28g) sang pytest chính thức.
"""


def test_session_dead_alert_with_cooldown(at_module, monkeypatch):
    at, page = at_module.at, at_module.page
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    cfg = at.load_config()
    cfg["enabled"] = True; cfg["dry_run"] = True
    at.save_config(cfg)

    from .conftest import FakePage

    # 1. Phien chet luc dang co tin hieu -> GUI canh bao Telegram
    FakePage.sim_has_dialog = True
    monkeypatch.setattr(at, "_last_session_alert_ts", 0.0)
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(not ok, f"submit tu choi dung: {msg}")
    chk(len(at_module.sent) == 1, f"da gui canh bao Telegram ({len(at_module.sent)} lan)")
    chk("bỏ lỡ" in at_module.sent[0].lower(), "noi dung canh bao noi ro bo lo lenh")

    # 2. Cooldown 15 phut: goi lai ngay -> KHONG gui them
    at_module.sent.clear()
    ok, msg = at.submit_signal("SHORT", strong=True, price=1985.0)
    chk(not ok, "van tu choi dung (an toan khong doi)")
    chk(len(at_module.sent) == 0, f"KHONG gui them trong cooldown ({len(at_module.sent)} lan)")

    # 3. Het cooldown (gia lap qua 15 phut) -> gui lai duoc
    monkeypatch.setattr(at, "_last_session_alert_ts",
                        at._last_session_alert_ts - (at._SESSION_ALERT_COOLDOWN_SEC + 5))
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(len(at_module.sent) == 1, f"gui lai duoc sau khi het cooldown ({len(at_module.sent)} lan)")

    # 4. Phien con song -> KHONG gui canh bao nao
    at_module.sent.clear()
    FakePage.sim_has_dialog = False
    monkeypatch.setattr(at, "_last_session_alert_ts", 0.0)
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(ok, f"submit thanh cong khi phien song: {msg}")
    chk(len(at_module.sent) == 0, f"KHONG gui canh bao gi khi phien binh thuong ({len(at_module.sent)} lan)")

    assert not fails, "\n" + "\n".join(fails)
