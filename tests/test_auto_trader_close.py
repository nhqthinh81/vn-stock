"""Kiểm chứng close_position(): dùng ClosePosition() JS thật của VPS.

Chuyển từ scratchpad/test_close_position.py (phase 28i) sang pytest chính thức.
"""


def test_close_position(at_module, monkeypatch):
    at, page = at_module.at, at_module.page
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    cfg = at.load_config()
    cfg["enabled"] = True; cfg["dry_run"] = False
    cfg["max_daily_loss_vnd"] = 0
    at.save_config(cfg)

    # 1. Dang giu SHORT -> type='B' -> bam nut LONG
    page.sim_position = ("SHORT", 1)   # vi the that THAT dang co (gate Phase 28j)
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(ok, f"dong OK: {msg}")
    chk(len(page.evaluated_close) == 1 and page.evaluated_close[0][1] == "B",
        f"type dung 'B': {page.evaluated_close}")
    chk("#btn_long" in page.clicked, "bam dung nut LONG de mua ve dong SHORT")
    chk("#btn_short" not in page.clicked, "KHONG bam nut SHORT")

    # 2. Dang giu LONG -> type='S' -> bam nut SHORT
    page.clicked.clear(); page.evaluated_close.clear()
    page.sim_position = ("LONG", 1)
    ok, msg = at.close_position("LONG", qty=1, price=1985.0)
    chk(ok, f"dong OK: {msg}")
    chk(page.evaluated_close[0][1] == "S", f"type dung 'S': {page.evaluated_close}")
    chk("#btn_short" in page.clicked, "bam dung nut SHORT de ban ve dong LONG")
    chk("#btn_long" not in page.clicked, "KHONG bam nut LONG")

    # 3. dry_run=True -> dien qua ClosePosition() nhung KHONG bam
    cfg["dry_run"] = True; at.save_config(cfg)
    page.clicked.clear(); page.evaluated_close.clear()
    page.sim_position = ("SHORT", 1)
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(ok, f"dry-run OK: {msg}")
    chk(len(page.evaluated_close) == 1, "van goi ClosePosition() de dien")
    chk("#btn_long" not in page.clicked, "KHONG bam nut trong dry-run")
    cfg["dry_run"] = False; at.save_config(cfg)

    # 4. enabled=False -> tu choi ngay, canh bao ro van con mo
    cfg["enabled"] = False; at.save_config(cfg)
    page.clicked.clear(); page.evaluated_close.clear()
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(not ok, f"tu choi dung: {msg}")
    chk(len(page.clicked) == 0 and len(page.evaluated_close) == 0, "khong dung browser")
    chk("chưa" in msg.lower() or "vẫn còn" in msg.lower(),
        "thong bao ro vi the that CHUA duoc dong")
    cfg["enabled"] = True; at.save_config(cfg)

    # 5. Phien chet -> tu choi + canh bao Telegram
    from .conftest import FakePage
    FakePage.sim_has_dialog = True
    page.clicked.clear(); page.evaluated_close.clear(); at_module.sent.clear()
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(not ok, f"tu choi khi phien chet: {msg}")
    chk(len(at_module.sent) == 1, f"gui canh bao Telegram ({len(at_module.sent)} lan)")
    chk("đóng được" in at_module.sent[0].lower() or "vẫn còn mở" in at_module.sent[0].lower(),
        "canh bao noi ro KHONG dong duoc + van con mo")
    FakePage.sim_has_dialog = False

    # 6. KHONG bi chan boi max_orders_per_day du da cham tran
    cfg["max_orders_per_day"] = 1; at.save_config(cfg)
    at._bump_orders_today()
    page.sim_position = ("SHORT", 1)
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(ok, f"van dong duoc du cham tran SO LENH MO: {msg}")
    cfg["max_orders_per_day"] = 6; at.save_config(cfg)

    # 7. KHONG bi chan boi max_daily_loss_vnd du da CHAM/VUOT tran
    cfg["max_daily_loss_vnd"] = 1_000_000; at.save_config(cfg)
    monkeypatch.setattr(at, "_today_realized_loss_vnd", lambda qty: 5_000_000.0)
    page.sim_position = ("SHORT", 1)
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(ok, f"van dong duoc du VUOT tran LO (giam rui ro, khong bi chan): {msg}")
    monkeypatch.setattr(at, "_today_realized_loss_vnd", lambda qty: 0.0)
    cfg["max_daily_loss_vnd"] = 0; at.save_config(cfg)

    # 8. Mo hop dong da cu (gia lech xa) -> tu choi truoc khi dong
    page.evaluated_close.clear()
    page.sim_position = ("SHORT", 1)
    ok, msg = at.close_position("SHORT", qty=1, price=2200.0)  # lech >3% so 1981.5
    chk(not ok, f"tu choi khi nghi mo da cu: {msg}")
    chk(len(page.evaluated_close) == 0, "khong goi ClosePosition() khi nghi mo da cu")

    # 9. Thieu ham ClosePosition() tren trang -> loi ro rang
    page.sim_position = ("SHORT", 1)

    def evaluate_no_close_fn(js, arg=None):
        if "ClosePosition" in js:
            return False
        return page.__class__.evaluate(page, js, arg)
    page.evaluate = evaluate_no_close_fn
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(not ok, f"tu choi khi thieu ham: {msg}")
    chk("ClosePosition" in msg, "thong bao neu dich danh ten ham thieu")

    # 10. VPS KHONG co vi the nao (da phang) -> tu choi, KHONG mo lenh nguoc tran trui
    del page.evaluate
    page.evaluated_close.clear(); page.clicked.clear()
    page.sim_position = ("NONE", 0)
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(not ok, f"tu choi khi VPS da phang: {msg}")
    chk(len(page.evaluated_close) == 0 and len(page.clicked) == 0,
        "khong dung ClosePosition()/click gi khi khong co vi the that")

    # 11. Vi the that KHAC chieu engine muon dong -> tu choi, khong doan mo
    page.sim_position = ("LONG", 1)   # engine tuong dang giu SHORT nhung that ra la LONG
    ok, msg = at.close_position("SHORT", qty=1, price=1985.0)
    chk(not ok, f"tu choi khi vi the that khac chieu: {msg}")
    chk(len(page.evaluated_close) == 0, "khong dung ClosePosition() khi vi the khac chieu")

    assert not fails, "\n" + "\n".join(fails)
