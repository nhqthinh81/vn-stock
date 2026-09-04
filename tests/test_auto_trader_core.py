"""Kiểm chứng auto_trader: mọi lớp an toàn + hành vi điền-sẵn vs đặt-tự-động.

Chuyển từ scratchpad/test_autotrader.py (phase 28) sang pytest chính thức.
"""
from .conftest import FULL_SELECTORS


def test_auto_trader_full_flow(at_module):
    at, page = at_module.at, at_module.page
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    # 1. enabled=false -> khong dung browser, chi ghi log
    cfg = at.load_config(); cfg["enabled"] = False; at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", strong=True, price=1981.5)
    chk(not ok, f"tra ve False khi tat: {msg}")
    chk(len(page.clicked) == 0, "khong click gi ca")

    # 2. enabled=true, chua co selector -> loi ro rang, khong click
    cfg["enabled"] = True; cfg["dry_run"] = True
    cfg["selectors"] = {k: "" for k in FULL_SELECTORS}
    at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", strong=True, price=1981.5)
    chk(not ok, f"tra ve False khi thieu selector: {msg}")
    chk("selector" in msg.lower(), "thong bao noi ro thieu selector")
    chk(len(page.clicked) == 0, "khong click gi (dung o buoc dien)")

    # 3. Mo hop dong da cu: gia trang lech qua nguong -> tu choi truoc khi dien
    cfg["selectors"] = dict(FULL_SELECTORS); at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", strong=True, price=2100.0)  # lech >3%
    chk(not ok, f"tu choi khi mo hop dong nghi da cu: {msg}")
    chk("het han" in msg.lower() or "cũ" in msg or "HẾT HẠN" in msg,
        "thong bao noi ro nghi ma cu")
    chk(len(page.selected) == 0 and len(page.filled) == 0,
        "KHONG dien gi khi phat hien mo cu")

    # 4. Gia khop (trong nguong) -> di qua duoc buoc kiem tra
    ok, msg = at.submit_signal("SHORT", strong=False, price=1985.0)  # lech ~0.18%
    chk(ok, f"gia khop nguong -> di tiep: {msg}")

    # 5. Tin hieu THUONG -> chon ma + dien gia/KL, KHONG bam LONG/SHORT
    ok, msg = at.submit_signal("SHORT", strong=False, price=1985.0)
    chk(ok, f"dien san thanh cong: {msg}")
    chk(("#right_stock_cd", "41I1G9000") in page.selected, "chon dung ma hop dong")
    chk("#btn_short" not in page.clicked and "#btn_long" not in page.clicked,
        "KHONG bam nut LONG/SHORT voi lenh thuong (o VPS bam = gui lenh)")
    chk(("#sohopdong", "1") in page.filled, "dien khoi luong")

    # 6. Tin hieu MANH, dry_run=True -> dien nhung KHONG bam
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(ok, f"dry-run OK: {msg}")
    chk("#btn_long" not in page.clicked, "dry-run KHONG bam nut LONG")
    chk("dry" in msg.lower(), "thong bao noi ro la dry-run")

    # 7. Tin hieu MANH, dry_run=False -> BAM THAT (gui lenh)
    cfg["dry_run"] = False; at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(ok, f"dat lenh OK: {msg}")
    chk("#btn_long" in page.clicked, "CO bam nut LONG (o VPS day la nut gui lenh)")
    chk("#acceptCreateOrderNew" in page.clicked, "co bam nut xac nhan modal")
    chk(at._orders_today() == 1, f"bo dem lenh/ngay tang len 1 (duoc {at._orders_today()})")

    # 8. Ngoai gio giao dich -> tu choi truoc khi dung browser
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0, in_session=False)
    chk(not ok, f"tu choi ngoai phien: {msg}")

    # 9. Cham tran lenh/ngay -> tu choi
    cfg["max_orders_per_day"] = 1; at.save_config(cfg)
    ok, msg = at.submit_signal("SHORT", strong=True, price=1985.0)
    chk(not ok, f"tu choi khi cham tran: {msg}")

    # 10. price=None -> bo qua kiem tra mo cu (khong chan oan)
    cfg["max_orders_per_day"] = 6; at.save_config(cfg)
    ok, msg = at.submit_signal("SHORT", strong=False, price=None)
    chk(ok, f"khong co gia van dien duoc (bo qua kiem tra): {msg}")

    # 11. Config ghi nguyen tu + doc lai dung
    cfg2 = at.load_config()
    chk(cfg2["symbol_code"] == "41I1G9000", "doc lai dung ma hop dong mac dinh")

    assert not fails, "\n" + "\n".join(fails)


def test_check_session_detects_expiry(at_module):
    """`check_session()` — 4 mức tin cậy, kiểm theo đúng thứ tự nhanh -> chậm."""
    at, page = at_module.at, at_module.page
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    # 12a. URL da chuyen sang login=true -> HET HAN (nhanh nhat)
    from .conftest import FakePage
    FakePage.url = "https://smartpro.vps.com.vn/v1/?login=true&redirect=..."
    ok, msg = at.check_session()
    chk(not ok, f"phat hien qua URL login=true: {msg}")
    FakePage.url = "https://smartpro.vps.com.vn/v1/"

    # 12b. Hop thoai 'dang nhap lai' (bootbox that cua loginConfirm)
    FakePage.sim_has_dialog = True
    ok, msg = at.check_session()
    chk(not ok, f"phat hien qua hop thoai bootbox: {msg}")
    chk("đăng nhập lại" in msg.lower() or "HẾT HẠN" in msg,
        "thong bao dung ten hop thoai that")
    FakePage.sim_has_dialog = False

    # 12. Mac dinh (con song) -> khong chan
    ok, msg = at.check_session()
    chk(ok, f"phien con song -> OK: {msg}")

    # 13. Co truong mat khau -> HET HAN (tin cay cao)
    FakePage.sim_has_pwd = True
    ok, msg = at.check_session()
    chk(not ok, f"phat hien het han qua truong mat khau: {msg}")
    chk("HẾT HẠN" in msg, "thong bao noi ro HET HAN (tin cay cao)")
    FakePage.sim_has_pwd = False

    # 14. Thieu phieu lenh, khong co mat khau -> nghi ngo (2 kha nang)
    FakePage.sim_has_ticket = False
    ok, msg = at.check_session()
    chk(not ok, f"phat hien thieu phieu lenh: {msg}")
    chk("có thể" in msg.lower() or "màn hình khác" in msg,
        "thong bao neu ca 2 kha nang, khong khang dinh sai")
    FakePage.sim_has_ticket = True

    # 15. submit_signal TU CHOI khi phien chet — TRUOC ca kiem tra mo cu
    FakePage.sim_has_pwd = True
    cfg = at.load_config(); cfg["enabled"] = True; cfg["dry_run"] = True
    cfg["selectors"] = dict(FULL_SELECTORS)
    at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", strong=True, price=1985.0)
    chk(not ok, f"tu choi khi phien chet: {msg}")
    chk(len(page.selected) == 0 and len(page.filled) == 0,
        "KHONG dung den buoc dien/chon ma khi phien da chet")

    assert not fails, "\n" + "\n".join(fails)
