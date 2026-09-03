"""Đặt lệnh tự động VPS SmartPro qua trình duyệt đang mở (Chrome DevTools Protocol).

Cách hoạt động
--------------
Chrome chạy sẵn với cờ ``--remote-debugging-port=9222`` (dùng ``Chay_Chrome_AutoTrade.bat``,
profile riêng), người dùng tự đăng nhập SmartPro + nhập PIN trong cửa sổ đó.
Mỗi lệnh: mở kết nối CDP mới → tìm tab VPS → điền phiếu lệnh → (tuỳ chế độ) bấm.
Không giữ kết nối lâu dài — trình duyệt khởi động lại cũng không cần sửa gì.

Chính sách theo tín hiệu (user chọn 26/08/2026):
  ⭐ MẠNH  → đặt lệnh TỰ ĐỘNG (khi enabled=true và dry_run=false)
  thường   → chỉ ĐIỀN SẴN phiếu lệnh, người dùng tự bấm xác nhận

An toàn — nhiều lớp, lớp nào cũng chặn được:
  1. ``enabled`` mặc định false — bật thủ công trong config/UI
  2. ``dry_run`` mặc định true — điền form, chụp màn hình, KHÔNG bấm
  3. Trần cứng: ``max_qty`` (mặc định 1 HĐ), ``max_orders_per_day``
  4. Chỉ trong giờ giao dịch; bộ đếm lệnh/ngày bền trên đĩa
  5. Mọi hành động ghi ``data/autotrade_log.txt`` + chụp màn hình

Module KHÔNG import streamlit — gọi được từ thread (như _send_telegram).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime

from .alerter import send_telegram as _tg_send, tg_escape as _tg_esc


def _ensure_win_proactor_policy() -> None:
    """Ép lại ProactorEventLoopPolicy trên Windows trước khi gọi Playwright.

    Streamlit chạy trên Tornado, và Tornado tự đặt process-wide asyncio policy
    thành WindowsSelectorEventLoopPolicy trên Windows. Playwright (kể cả
    connect_over_cdp) vẫn cần spawn tiến trình driver nội bộ để nói chuyện CDP
    qua websocket — SelectorEventLoop KHÔNG hỗ trợ subprocess trên Windows,
    lỗi `NotImplementedError` ở `asyncio.create_subprocess_exec`. Vì auto_trader
    luôn chạy trong thread nền (threading.Thread), nó thừa hưởng policy sai đó.

    Đặt lại policy là thao tác toàn tiến trình, nhưng vô hại với Streamlit:
    Tornado đã tạo xong IOLoop của nó từ trước (lúc khởi động server), object
    loop đã tồn tại không đổi theo policy nữa — chỉ các loop MỚI tạo sau lệnh
    này (đúng cái Playwright sắp tạo) mới theo policy mới.
    """
    if sys.platform != "win32":
        return
    try:
        if not isinstance(asyncio.get_event_loop_policy(),
                          asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG_FILE   = os.path.join(_APP_DIR, "data", "autotrade_config.json")
_STATE_FILE = os.path.join(_APP_DIR, "data", "autotrade_state.json")
_LOG_FILE   = os.path.join(_APP_DIR, "data", "autotrade_log.txt")
_SHOT_DIR   = os.path.join(_APP_DIR, "data", "autotrade_shots")

_LOCK = threading.Lock()          # 1 lệnh browser tại 1 thời điểm là đủ

# Cảnh báo Telegram khi phiên VPS chết TẠI THỜI ĐIỂM có tín hiệu thật (đường
# phản ứng tức thời) — khác với quét định kỳ ~60 phút bên phaisinh_tab.py (đường
# quan sát thụ động, chạy dù không có tín hiệu nào). Hai đường bổ sung cho nhau:
# đường này báo ngay khi một lệnh THẬT vừa bị bỏ lỡ; đường kia vẫn cảnh báo
# ngay cả những lúc không có tín hiệu nào fire trong hàng giờ.
_SESSION_ALERT_COOLDOWN_SEC = 900   # 15 phút — tránh spam khi nhiều tín hiệu
_last_session_alert_ts = 0.0        # module-level, đủ dùng trong 1 tiến trình
_SESSION_ALERT_LOCK = threading.Lock()


def _alert_session_dead(msg: str) -> None:
    """Gửi Telegram khi phát hiện phiên VPS chết ngay lúc đang cố đặt lệnh.

    Có cooldown riêng (15 phút) — độc lập với `_TG_DEDUP_SEC` (chặn tin trùng
    HỆT nội dung) vì tin nhắn ở đây có thể khác nhau tuỳ mức tin cậy phát hiện
    (`_session_alive`), nên so nội dung y hệt không đủ chặn spam.
    """
    global _last_session_alert_ts
    with _SESSION_ALERT_LOCK:
        now = time.time()
        if now - _last_session_alert_ts < _SESSION_ALERT_COOLDOWN_SEC:
            return
        _last_session_alert_ts = now
    _tg_send(
        f"⛔ <b>#VN30F1M Auto-trade: BỎ LỠ lệnh do phiên VPS hết hạn</b>\n"
        f"{_tg_esc(msg)}\n"
        f"⚡ Vừa có tín hiệu cần đặt lệnh nhưng KHÔNG thực hiện được — "
        f"đăng nhập lại ngay để không bỏ lỡ lệnh tiếp theo."
    )

_DEFAULT_CFG = {
    "enabled": False,             # công tắc tổng — false thì mọi lệnh chỉ ghi log
    "dry_run": True,              # true: điền + chụp màn hình, KHÔNG bấm nút đặt
    "cdp_url": "http://127.0.0.1:9222",
    "page_url_contains": "vps.com.vn",
    "symbol": "VN30F1M",
    "max_qty": 1,
    "max_orders_per_day": 6,
    # Mã hợp đồng NỘI BỘ của VPS cho VN30F1M — KHÔNG phải "VN30F1M".
    # Xác nhận 28/08/2026 qua 3 nguồn độc lập: (1) option đang selected sẵn
    # trong #right_stock_cd, (2) dòng "active" trong bảng theo dõi phái sinh,
    # đáo hạn gần nhất 17/09/2026, (3) giá khớp đúng feed nội bộ (1.981,5 lúc
    # 14:45). VN30F1M là hợp đồng THÁNG GẦN NHẤT — đáo hạn Thứ Năm tuần thứ 3
    # MỖI THÁNG (không phải theo quý). Mã này vì vậy đổi HÀNG THÁNG.
    # Không chỉ dựa vào việc nhớ cập nhật tay: `_verify_symbol_price()` so giá
    # hiển thị của mã này trên trang với giá từ feed nội bộ trước mỗi lệnh —
    # lệch quá xa thì TỪ CHỐI đặt lệnh và báo rõ, thay vì đặt nhầm hợp đồng.
    "symbol_code": "41I1G9000",
    "max_price_drift_pct": 3.0,   # lệch > ngưỡng này (%) coi như mã đã cũ
    # Selector CSS của phiếu lệnh SmartPro — điền bằng inspect_vps.py.
    # Còn trống thì mọi lệnh dừng ở bước "chưa cấu hình" (an toàn mặc định).
    # Kiến trúc thật của VPS SmartPro (khác giả định ban đầu): #right_stock_cd
    # là <select>, và nút LONG/SHORT chính là nút GỬI LỆNH luôn — không có nút
    # "Đặt lệnh" tách riêng. Click LONG/SHORT xong mới hiện modal xác nhận
    # (nếu bật "Xác nhận trước khi đặt lệnh").
    "selectors": {
        "symbol_select": "#right_stock_cd",   # <select> — dùng select_option
        "price_input":   "#right_price",
        "qty_input":     "#sohopdong",
        "long_button":   "#btn_long",         # bấm = GỬI LỆNH LONG luôn
        "short_button":  "#btn_short",        # bấm = GỬI LỆNH SHORT luôn
        "confirm_button": "#acceptCreateOrderNew"  # modal "Xác nhận" (nếu có)
    },
}


# ── Config / state / log ─────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = json.loads(json.dumps(_DEFAULT_CFG))          # deep copy
    try:
        if os.path.exists(_CFG_FILE):
            with open(_CFG_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k == "selectors" and isinstance(v, dict):
                    cfg["selectors"].update(v)
                else:
                    cfg[k] = v
    except Exception as e:
        _log(f"⚠️ Lỗi đọc config: {e} — dùng mặc định (enabled=false)")
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(_CFG_FILE), exist_ok=True)
    data = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
    tmp = _CFG_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, _CFG_FILE)


def _orders_today() -> int:
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return int(st.get("count", 0))
    except Exception:
        pass
    return 0


def _bump_orders_today() -> None:
    st = {"date": datetime.now().strftime("%Y-%m-%d"), "count": _orders_today() + 1}
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, _STATE_FILE)


def _log(line: str) -> None:
    stamp = datetime.now().strftime("%d/%m %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {line}\n")
    except Exception:
        pass


def read_log_tail(n: int = 15) -> list[str]:
    try:
        with open(_LOG_FILE, encoding="utf-8", errors="replace") as f:
            return f.read().strip().splitlines()[-n:][::-1]
    except Exception:
        return []


# ── Lõi browser ──────────────────────────────────────────────────────────────

def _find_vps_page(browser, url_part: str):
    for ctx in browser.contexts:
        for page in ctx.pages:
            if url_part in (page.url or ""):
                return page
    return None


def _shot(page, tag: str) -> str:
    try:
        os.makedirs(_SHOT_DIR, exist_ok=True)
        path = os.path.join(_SHOT_DIR,
                            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.png")
        page.screenshot(path=path)
        return os.path.basename(path)
    except Exception as e:
        return f"(chụp lỗi: {e})"


def check_connection() -> tuple[bool, str]:
    """Kiểm tra nhanh: Chrome debug-port có mở và có tab VPS không."""
    cfg = load_config()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Thiếu playwright — pip install playwright"
    _ensure_win_proactor_policy()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cfg["cdp_url"], timeout=5000)
            page = _find_vps_page(browser, cfg["page_url_contains"])
            n_tabs = sum(len(c.pages) for c in browser.contexts)
            browser.close()
            if page is None:
                return False, (f"Nối được Chrome ({n_tabs} tab) nhưng KHÔNG có tab "
                               f"chứa '{cfg['page_url_contains']}' — hãy mở SmartPro "
                               f"trong đúng cửa sổ Chrome đó")
            return True, f"OK — thấy tab VPS: {page.url[:70]}"
    except Exception as e:
        return False, (f"Không nối được {cfg['cdp_url']}: {type(e).__name__}. "
                       f"Chrome phải chạy bằng Chay_Chrome_AutoTrade.bat")


def _session_alive(page) -> tuple[bool, str]:
    """Kiểm tra phiên đăng nhập VPS còn sống + đang ở đúng màn hình đặt lệnh.

    Phiên SmartPro tự đăng xuất sau 720 phút — nếu không phát hiện, bot vẫn
    tưởng đã nối được Chrome (đúng — Chrome vẫn mở) nhưng thật ra đang điền
    vào trang đăng nhập, không có tác dụng gì và không báo lỗi rõ ràng.

    Xác nhận 03/09/2026 bằng cách đọc `Common/js/app.js` thật của SmartPro:
    hết hạn được server phát hiện qua lỗi AJAX `FOException.InvalidSessionException`
    / `NotLoginException`, xử lý bởi `loginConfirm()` — hiện hộp thoại "Hệ thống
    yêu cầu đăng nhập lại!", xoá cookie, rồi chuyển hướng sang `?login=true`.
    KHÔNG có bộ đếm ngược phía trình duyệt (không tìm thấy heartbeat/ping nào
    giữ phiên sống bằng hoạt động) — hạn 720 phút là tuyệt đối kể từ lúc đăng
    nhập, không gia hạn được. Cookie phiên (`ASP.NET_SessionId`) đặt cờ HttpOnly
    nên không đọc được bằng `document.cookie` — không dùng làm tín hiệu được.

    Bốn mức tin cậy, kiểm tra theo thứ tự nhanh → chậm:
      1. URL đã chứa `login=true` → chắc chắn đã bị chuyển hướng sang đăng nhập.
      2. Hộp thoại "đăng nhập lại" (bootbox của chính `loginConfirm()`) đang hiện.
      3. Có trường mật khẩu HIỆN HỮU trên trang → gần như chắc chắn hết hạn.
      4. Không có (1-3) nhưng thiếu `#right_stock_cd` (phiếu lệnh) → có thể đã
         hết hạn HOẶC cửa sổ đang ở màn hình khác — không phân biệt được từ
         bên ngoài nên báo cả hai khả năng thay vì khẳng định sai.
    """
    try:
        if "login=true" in (page.url or ""):
            return False, ("Phiên VPS đã HẾT HẠN — trang đã chuyển sang màn hình đăng "
                           "nhập. Đăng nhập lại trong cửa sổ Chrome đặt lệnh tự động.")
    except Exception:
        pass
    try:
        has_dialog = page.evaluate(
            "() => { const b = document.querySelector('.bootbox'); "
            "return !!(b && b.innerText && b.innerText.includes('đăng nhập lại')); }"
        )
    except Exception as e:
        return False, f"Không đọc được trang: {type(e).__name__}"
    if has_dialog:
        return False, ("Phiên VPS đã HẾT HẠN — trang đang hiện hộp thoại 'Hệ thống "
                       "yêu cầu đăng nhập lại!'. Đăng nhập lại trong cửa sổ Chrome.")
    try:
        has_pwd = page.evaluate(
            "() => { const el = document.querySelector('input[type=\"password\"]'); "
            "return !!(el && el.offsetParent !== null); }"
        )
    except Exception as e:
        return False, f"Không đọc được trang: {type(e).__name__}"
    if has_pwd:
        return False, ("Phiên đăng nhập VPS đã HẾT HẠN (thấy ô mật khẩu trên trang). "
                       "Đăng nhập lại trong cửa sổ Chrome đặt lệnh tự động.")
    try:
        has_ticket = page.evaluate("() => !!document.getElementById('right_stock_cd')")
    except Exception as e:
        return False, f"Không đọc được trang: {type(e).__name__}"
    if not has_ticket:
        return False, ("Không thấy phiếu lệnh phái sinh trên trang — có thể phiên đã "
                       "hết hạn, hoặc cửa sổ đang ở màn hình khác. Mở lại "
                       "smartpro.vps.com.vn/v1/ và vào tab Giao dịch phái sinh.")
    return True, "OK"


def check_session() -> tuple[bool, str]:
    """Kiểm tra đầy đủ: nối được Chrome + có tab VPS + phiên còn sống.

    Dùng cho cả nút bấm tay trong panel lẫn kiểm tra định kỳ tự động trong
    engine (mỗi ~60 phút khi auto-trade đang bật) — xem `_render_autotrade_panel`
    và hook trong `_live_panel_body`.
    """
    ok, msg = check_connection()
    if not ok:
        return False, msg
    cfg = load_config()
    _ensure_win_proactor_policy()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cfg["cdp_url"], timeout=5000)
            page = _find_vps_page(browser, cfg["page_url_contains"])
            if page is None:
                browser.close()
                return False, "Mất tab VPS giữa chừng"
            alive, sess_msg = _session_alive(page)
            browser.close()
            return alive, ("Phiên VPS còn sống — sẵn sàng giao dịch" if alive else sess_msg)
    except Exception as e:
        return False, f"Lỗi kiểm tra phiên: {type(e).__name__}: {str(e)[:100]}"


def _verify_symbol_price(page, cfg: dict, our_price: float | None) -> str | None:
    """Chặn đặt lệnh nhầm hợp đồng đã cũ — mã VN30F1M nội bộ đổi HÀNG THÁNG.

    So giá hiển thị trên trang cho `symbol_code` hiện cấu hình với giá phía
    ta đang định đặt (`our_price`, lấy từ feed 1 phút nội bộ). Nếu lệch quá
    `max_price_drift_pct`, gần như chắc chắn `symbol_code` đã hết hạn (đang
    trỏ vào hợp đồng tháng trước) — từ chối thay vì đặt nhầm bằng tiền thật.

    Trả None nếu ổn, chuỗi lỗi nếu lệch/không đọc được giá.
    """
    if our_price is None or our_price <= 0:
        return None                      # không có giá để so — bỏ qua, không chặn oan
    code = cfg["symbol_code"]
    try:
        page_price = page.evaluate(
            "(id) => { const el = document.getElementById(id); "
            "return el ? parseFloat(el.textContent.replace(/,/g, '')) : null; }",
            f"{code}pri",
        )
    except Exception as e:
        return f"Không đọc được giá trang cho {code}: {type(e).__name__}"
    if page_price is None or page_price <= 0:
        return (f"Không thấy dòng giá cho mã {code} trên trang — mã có thể đã "
                f"hết hạn. Chạy lại inspect_vps.py để lấy mã VN30F1M hiện tại.")
    drift = abs(page_price - our_price) / our_price * 100
    if drift > cfg.get("max_price_drift_pct", 3.0):
        return (f"Giá mã {code} trên trang ({page_price:.1f}) lệch {drift:.1f}% so "
                f"với giá nội bộ ({our_price:.1f}) — nghi mã ĐÃ HẾT HẠN (VN30F1M "
                f"đáo hạn hàng tháng). Chạy lại inspect_vps.py để cập nhật "
                f"symbol_code, KHÔNG đặt lệnh với mã cũ.")
    return None


def _fill_ticket(page, cfg: dict, side: str, qty: int, price: float | None) -> str | None:
    """Điền phiếu lệnh — KHÔNG đụng nút LONG/SHORT.

    Quan trọng: ở VPS SmartPro, nút LONG/SHORT chính là nút GỬI LỆNH (không có
    nút "Đặt lệnh" tách riêng). Nên hàm điền phải dừng lại trước bước đó — việc
    có bấm hay không do `_submit()` quyết định, tuỳ chính sách MẠNH/thường và
    dry-run. Trả None nếu ổn, chuỗi lỗi nếu thiếu selector/element.
    """
    sel = cfg["selectors"]
    need = ["symbol_select", "qty_input",
            "long_button" if side == "LONG" else "short_button"]
    missing = [k for k in need if not sel.get(k)]
    if missing:
        return f"Chưa cấu hình selector: {', '.join(missing)} (chạy inspect_vps.py)"

    try:
        page.select_option(sel["symbol_select"], value=cfg["symbol_code"], timeout=4000)
        if price is not None and sel.get("price_input"):
            page.fill(sel["price_input"], f"{price:.1f}", timeout=4000)
        page.fill(sel["qty_input"], str(qty), timeout=4000)
        return None
    except Exception as e:
        return f"Điền phiếu lỗi: {type(e).__name__}: {str(e)[:120]}"


def _submit(page, cfg: dict, side: str) -> str | None:
    """Bấm nút LONG/SHORT — ở VPS đây LÀ hành động gửi lệnh, không phải chọn chiều."""
    sel = cfg["selectors"]
    side_btn = sel.get("long_button") if side == "LONG" else sel.get("short_button")
    if not side_btn:
        return f"Chưa cấu hình selector nút {side}"
    try:
        page.click(side_btn, timeout=4000)
        if sel.get("confirm_button"):
            try:
                page.click(sel["confirm_button"], timeout=4000)
            except Exception:
                pass                     # nhiều khi không bật "Xác nhận trước khi đặt lệnh"
        return None
    except Exception as e:
        return f"Bấm gửi lệnh lỗi: {type(e).__name__}: {str(e)[:120]}"


# ── API chính ────────────────────────────────────────────────────────────────

def submit_signal(side: str, strong: bool, price: float | None = None,
                  in_session: bool = True) -> tuple[bool, str]:
    """Xử lý 1 tín hiệu theo chính sách: MẠNH → đặt tự động, thường → điền sẵn.

    Trả (đã_thao_tác_browser, mô_tả). Mọi nhánh đều ghi log.
    """
    cfg = load_config()
    tag = "⭐MẠNH" if strong else "thường"

    if not cfg.get("enabled"):
        _log(f"⏸️ {side} ({tag}) — auto trade đang TẮT, bỏ qua")
        return False, "Auto trade đang tắt"
    if not in_session:
        _log(f"⛔ {side} ({tag}) — ngoài giờ giao dịch, bỏ qua")
        return False, "Ngoài giờ giao dịch"
    if _orders_today() >= int(cfg.get("max_orders_per_day", 6)):
        _log(f"⛔ {side} ({tag}) — chạm trần {cfg['max_orders_per_day']} lệnh/ngày")
        return False, "Chạm trần lệnh/ngày"

    qty = min(1, int(cfg.get("max_qty", 1))) if strong else int(cfg.get("max_qty", 1))
    qty = max(1, min(qty, int(cfg.get("max_qty", 1))))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("❌ Thiếu playwright")
        return False, "Thiếu playwright"
    _ensure_win_proactor_policy()

    with _LOCK:
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(cfg["cdp_url"], timeout=5000)
                page = _find_vps_page(browser, cfg["page_url_contains"])
                if page is None:
                    _log(f"❌ {side} ({tag}) — không thấy tab VPS")
                    browser.close()
                    return False, "Không thấy tab VPS trong Chrome debug"

                alive, sess_msg = _session_alive(page)
                if not alive:
                    shot = _shot(page, "session_dead")
                    _log(f"⛔ {side} ({tag}) — {sess_msg} · ảnh {shot}")
                    _alert_session_dead(sess_msg)
                    browser.close()
                    return False, sess_msg

                err = _verify_symbol_price(page, cfg, price)
                if err:
                    shot = _shot(page, "stale_symbol")
                    _log(f"⛔ {side} ({tag}) — {err} · ảnh {shot}")
                    browser.close()
                    return False, err

                err = _fill_ticket(page, cfg, side, qty, price)
                if err:
                    shot = _shot(page, "fill_err")
                    _log(f"❌ {side} ({tag}) — {err} · ảnh {shot}")
                    browser.close()
                    return False, err

                # Lệnh thường: dừng ở điền sẵn — người dùng tự bấm
                if not strong:
                    shot = _shot(page, "prefill")
                    _log(f"📝 ĐIỀN SẴN {side} x{qty} (thường) — chờ người dùng bấm "
                         f"· ảnh {shot}")
                    browser.close()
                    return True, f"Đã điền sẵn {side} x{qty} — bạn bấm xác nhận"

                # Lệnh MẠNH: đặt tự động (trừ khi dry-run)
                if cfg.get("dry_run", True):
                    shot = _shot(page, "dryrun")
                    _log(f"🧪 DRY-RUN {side} x{qty} (MẠNH) — đã điền, KHÔNG bấm "
                         f"· ảnh {shot}")
                    browser.close()
                    return True, f"DRY-RUN: đã điền {side} x{qty}, không bấm"

                err = _submit(page, cfg, side)
                if err:
                    shot = _shot(page, "submit_err")
                    _log(f"❌ {side} (MẠNH) — {err} · ảnh {shot}")
                    browser.close()
                    return False, err

                _bump_orders_today()
                shot = _shot(page, "submitted")
                _log(f"✅ ĐÃ ĐẶT {side} x{qty} (MẠNH, lệnh thứ {_orders_today()} "
                     f"hôm nay) · ảnh {shot}")
                browser.close()
                return True, f"ĐÃ ĐẶT {side} x{qty}"
        except Exception as e:
            _log(f"❌ {side} ({tag}) — {type(e).__name__}: {str(e)[:150]}")
            return False, f"{type(e).__name__}: {str(e)[:80]}"


def submit_signal_async(side: str, strong: bool, price: float | None = None,
                        in_session: bool = True) -> None:
    """Bản chạy nền — gọi từ engine, không chặn render (như _send_telegram_async)."""
    threading.Thread(target=submit_signal,
                     args=(side, strong, price, in_session), daemon=True).start()
