"""Fixture dùng chung cho bộ test — đặc biệt là mô phỏng VPS SmartPro qua
Playwright bằng fake object (không cần Chrome thật) cho các test đụng tới
`vn_invest/auto_trader.py`.

Kiến trúc THẬT của VPS SmartPro (xác nhận 28/08/2026 qua inspect_vps.py):
  - #right_stock_cd là <select>, dùng select_option
  - nút LONG/SHORT (#btn_long/#btn_short) CHÍNH LÀ nút GỬI LỆNH, không có
    nút "Đặt lệnh" tách riêng
  - _fill_ticket KHÔNG đụng nút chiều lệnh; _submit(page, cfg, side) mới bấm
"""
import os
import sys
import types

import pytest

# Cho phep `import vn_invest.xxx` khi chay pytest tu bat ky thu muc nao
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Gia hien thi tren trang cho ma hop dong noi bo VN30F1M (dung trong _verify_symbol_price)
PAGE_PRICE = {"41I1G9000": 1981.5}

FULL_SELECTORS = {
    "symbol_select": "#right_stock_cd", "price_input": "#right_price",
    "qty_input": "#sohopdong", "long_button": "#btn_long",
    "short_button": "#btn_short", "confirm_button": "#acceptCreateOrderNew",
}


class FakePage:
    """Giả lập 1 tab Chrome đang mở VPS SmartPro — đủ để auto_trader.py chạy
    hết logic quyết định (không đụng Chrome/mạng thật).

    Kể từ Phase 28j: click LONG/SHORT không còn coi là "thành công" ngay —
    `_submit()` phải xác nhận qua `_newest_order_sig()`/`_read_position()`.
    FakePage mô phỏng 1 lệnh khớp thật bằng cách tự cập nhật `sim_position` +
    tăng số hiệu lệnh NGAY khi click (để vòng lặp xác nhận của `_submit()`
    thấy thay đổi ở lần kiểm tra đầu tiên, không phải chờ thật 6 giây).
    """

    url = "https://smartpro.vps.com.vn/v1/"
    frames = []
    sim_has_pwd = False        # gia lap: co truong mat khau (phien het han)?
    sim_has_ticket = True      # gia lap: co #right_stock_cd (dung man hinh)?
    sim_has_dialog = False     # gia lap: hop thoai "dang nhap lai" dang hien?
    sim_ticket_mode = "normal"  # 'normal' | 'condition' | 'unknown' (Phase 28j)

    def __init__(self):
        self.clicked = []
        self.filled = []
        self.selected = []
        self.evaluated_sltp = []
        self.evaluated_close = []
        self.sim_position = ("NONE", 0)   # vi the THAT dang co tren "san gia lap"
        self.sim_error_popup = None       # loi VPS hien ra sau khi bam (Phase 28j)
        self._order_seq = 0

    def click(self, sel, timeout=0):
        self.clicked.append(sel)
        if sel in ("#btn_long", "#btn_short") and not self.sim_error_popup:
            self._order_seq += 1
            self.sim_position = ("LONG" if sel == "#btn_long" else "SHORT", 1)

    def fill(self, sel, val, timeout=0):
        self.filled.append((sel, val))

    def select_option(self, sel, value=None, timeout=0):
        self.selected.append((sel, value))

    def screenshot(self, path=None):
        with open(path, "wb") as f:
            f.write(b"x")

    def wait_for_selector(self, sel, state=None, timeout=0):
        # Mac dinh: mo phong tai khoan CO bat "Xac nhan truoc khi dat lenh"
        # (modal hien ra) -> _submit() se bam. Test rieng muon mo phong
        # KHONG co modal thi tu ghi de attribute nay tren instance.
        return None

    def evaluate(self, js, arg=None):
        if "co.sltp.order.new" in js:
            self.evaluated_sltp.append(list(arg))
            return {"sent": True, "http_ok": True, "status": 200,
                    "text": '{"result":"ok"}'}
        if "ClosePosition" in js:
            self.evaluated_close.append(list(arg))
            return True
        if "select_normal_order" in js:
            label = {"normal": "Lenh thuong", "condition": "Lenh dieu kien",
                     "unknown": "khong xac dinh"}[self.sim_ticket_mode]
            return {"m": self.sim_ticket_mode, "l": label}
        if "tbl-status-danhmuc" in js:
            side, qty = self.sim_position
            return "0" if side == "NONE" else (str(qty) if side == "LONG" else str(-qty))
        if "account:value('#right_account')" in js:
            sel, side = arg
            filled = dict(self.filled)
            return {"account": "TEST", "symbol": "41I1G9000", "side": side,
                    "qty": int(filled.get(sel["qty_input"], 1)),
                    "price": float(filled.get(sel["price_input"], 1981.5))}
        if "order_normal" in js:
            return [{"id": str(1000 + self._order_seq), "status": "Đã khớp", "matches": True}] if self._order_seq else []
        if "toast-error" in js:
            return self.sim_error_popup
        if "bootbox" in js:
            return FakePage.sim_has_dialog
        if 'input[type="password"]' in js:
            return FakePage.sim_has_pwd
        if "right_stock_cd" in js and arg is None:
            return FakePage.sim_has_ticket
        code = arg.replace("pri", "") if arg else None
        return PAGE_PRICE.get(code)


class FakeCtx:
    def __init__(self, page):
        self.pages = [page]


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [FakeCtx(page)]

    def close(self):
        pass


class FakeChromium:
    def __init__(self, page):
        self._page = page

    def connect_over_cdp(self, url, timeout=0):
        return FakeBrowser(self._page)


class FakePW:
    def __init__(self, page):
        self.chromium = FakeChromium(page)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_page():
    """1 FakePage mới tinh cho mỗi test — reset cả state class-level."""
    FakePage.url = "https://smartpro.vps.com.vn/v1/"
    FakePage.sim_has_pwd = False
    FakePage.sim_has_ticket = True
    FakePage.sim_has_dialog = False
    return FakePage()


@pytest.fixture
def at_module(tmp_path, fake_page, monkeypatch):
    """`vn_invest.auto_trader` với file trạng thái trỏ vào tmp_path + playwright
    giả lập + `_today_realized_loss_vnd`/`_tg_send` đã bịt (không đụng mạng thật).

    Trả `types.SimpleNamespace(at=module, page=fake_page, sent=[])` — `sent`
    là danh sách tin Telegram đã "gửi" (qua `_tg_send` đã monkeypatch).
    """
    import vn_invest.auto_trader as at
    import vn_invest.autotrade_runtime as runtime
    monkeypatch.setattr(runtime, "STATE_PATH", tmp_path / "autotrade_live_state.json")

    monkeypatch.setattr(at, "_CFG_FILE", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(at, "_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(at, "_LOG_FILE", str(tmp_path / "log.txt"))
    monkeypatch.setattr(at, "_SHOT_DIR", str(tmp_path / "shots"))

    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: FakePW(fake_page)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    cfg = at.load_config()
    cfg["selectors"] = dict(FULL_SELECTORS)
    at.save_config(cfg)

    monkeypatch.setattr(at, "_today_realized_loss_vnd", lambda qty: 0.0)
    monkeypatch.setattr(at, "_last_session_alert_ts", 0.0)
    monkeypatch.setattr(at, "_last_ticket_alert_ts", 0.0)
    monkeypatch.setattr(at, "_last_loss_alert_day", None)
    sent = []
    monkeypatch.setattr(at, "_tg_send", lambda msg: (sent.append(msg), True)[1])

    return types.SimpleNamespace(at=at, page=fake_page, sent=sent)


@pytest.fixture
def ps_module(tmp_path, monkeypatch):
    """`vn_invest.phaisinh_tab` với file trạng thái ảo/journal trỏ vào tmp_path.

    KHÔNG chạm `st.session_state` ở đây — mỗi test tự `monkeypatch.setattr(
    ps.st, "session_state", {...})` vì cần mô phỏng nhiều "phiên" khác nhau.
    """
    import vn_invest.phaisinh_tab as ps

    monkeypatch.setattr(ps, "_PS_STATE_FILE", str(tmp_path / "ps_state.json"))
    monkeypatch.setattr(ps, "_JOURNAL_FILE", str(tmp_path / "journal.csv"))
    return ps
