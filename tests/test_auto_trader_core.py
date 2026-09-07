"""Kiểm chứng auto_trader: mọi lớp an toàn + hành vi điền-sẵn vs đặt-tự-động.

Chuyển từ scratchpad/test_autotrader.py (phase 28) sang pytest chính thức.
"""
from .conftest import FULL_SELECTORS


def test_auto_trader_prefill_is_not_a_live_order(at_module):
    at, page = at_module.at, at_module.page
    cfg = at.load_config()
    cfg.update(enabled=True, dry_run=True)
    at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", True, price=1981.5)
    assert ok and "DRY-RUN" in msg
    assert ("#right_stock_cd", "41I1G9000") in page.selected
    assert not page.clicked and at._orders_today() == 0
    cfg.update(dry_run=False, auto_all_signals=False)
    at.save_config(cfg)
    ok, msg = at.submit_signal("LONG", False, price=1981.5)
    assert ok and "điền sẵn" in msg
    assert not page.clicked


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
