"""Kiểm chứng auto_all_signals + trần lỗ ngày (kill-switch).

Chuyển từ scratchpad/test_loss_cap.py (phase 28h) sang pytest chính thức.
"""


def test_loss_cap_and_auto_all_signals(at_module, monkeypatch):
    at, page = at_module.at, at_module.page
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    cfg = at.load_config()
    cfg["enabled"] = True; cfg["dry_run"] = False   # de kiem tra CO bam hay khong
    cfg["max_daily_loss_vnd"] = 0                    # tat tran o nhom test dau
    at.save_config(cfg)

    # 1. auto_all_signals=False (mac dinh) — thuong CHI dien san
    cfg["auto_all_signals"] = False; at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", strong=False, price=1985.0)
    chk(ok, f"dien san OK: {msg}")
    chk("#btn_long" not in page.clicked, "KHONG bam voi thuong khi auto_all_signals tat")

    # 2. auto_all_signals=True — thuong CUNG tu bam gui
    cfg["auto_all_signals"] = True; at.save_config(cfg)
    page.clicked.clear()
    ok, msg = at.submit_signal("LONG", strong=False, price=1985.0)
    chk(ok, f"dat lenh OK: {msg}")
    chk("#btn_long" in page.clicked, "CO bam voi thuong khi auto_all_signals BAT")

    # 3. MANH van tu bam gui du auto_all_signals tat/bat (khong doi)
    cfg["auto_all_signals"] = False; at.save_config(cfg)
    page.clicked.clear()
    ok, msg = at.submit_signal("SHORT", strong=True, price=1985.0)
    chk(ok and "#btn_short" in page.clicked, "MANH luon tu bam, khong phu thuoc auto_all_signals")

    # 4. Tran lo TAT (0) — khong chan du lo bao nhieu
    cfg["auto_all_signals"] = True; cfg["max_daily_loss_vnd"] = 0; at.save_config(cfg)
    monkeypatch.setattr(at, "_today_realized_loss_vnd", lambda qty: 50_000_000.0)
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(ok, f"khong bi chan vi tran dang tat: {msg}")

    # 5. Duoi tran — di qua binh thuong
    cfg["max_daily_loss_vnd"] = 1_000_000; at.save_config(cfg)
    monkeypatch.setattr(at, "_today_realized_loss_vnd", lambda qty: 500_000.0)
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(ok, f"duoi tran van dat lenh binh thuong: {msg}")
    chk(len(at_module.sent) == 0, "khong gui canh bao khi con duoi tran")

    # 6. CHAM tran — TU CHOI truoc khi dung browser, gui canh bao
    monkeypatch.setattr(at, "_today_realized_loss_vnd", lambda qty: 1_000_000.0)
    page.clicked.clear(); page.selected.clear()
    ok, msg = at.submit_signal("SHORT", strong=True, price=1985.0)
    chk(not ok, f"tu choi khi cham tran: {msg}")
    chk(len(page.clicked) == 0 and len(page.selected) == 0,
        "KHONG dung browser (tu choi TRUOC khi noi Chrome)")
    chk(len(at_module.sent) == 1, f"gui canh bao Telegram ({len(at_module.sent)} lan)")
    chk("chạm trần lỗ" in at_module.sent[0].lower(), "noi dung canh bao dung")

    # 7. Van cham tran, goi lai lan 2 cung ngay -> KHONG gui canh bao lap
    at_module.sent.clear()
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(not ok, "van tu choi (dung)")
    chk(len(at_module.sent) == 0, f"KHONG gui lai canh bao trong cung ngay ({len(at_module.sent)} lan)")

    # 8. VUOT tran (khong chi cham) van chan
    monkeypatch.setattr(at, "_today_realized_loss_vnd", lambda qty: 1_500_000.0)
    monkeypatch.setattr(at, "_last_loss_alert_day", None)   # gia lap sang ngay moi
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(not ok, "van tu choi khi vuot tran")

    assert not fails, "\n" + "\n".join(fails)
