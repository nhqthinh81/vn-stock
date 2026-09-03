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


# Cảnh báo khi phiếu lệnh SmartPro bị để sai chế độ (Lệnh điều kiện / Stop Loss
# thay vì Lệnh thường) — cú bấm LONG/SHORT lúc đó tạo LỆNH ĐIỀU KIỆN chứ không
# phải lệnh vào, và có thể bị VPS từ chối âm thầm. Cooldown riêng 15 phút.
_TICKET_ALERT_COOLDOWN_SEC = 900
_last_ticket_alert_ts = 0.0
_TICKET_ALERT_LOCK = threading.Lock()


def _alert_ticket_mode(msg: str) -> None:
    global _last_ticket_alert_ts
    with _TICKET_ALERT_LOCK:
        now = time.time()
        if now - _last_ticket_alert_ts < _TICKET_ALERT_COOLDOWN_SEC:
            return
        _last_ticket_alert_ts = now
    _tg_send(
        f"⛔ <b>#VN30F1M Auto-trade: PHIẾU LỆNH SAI CHẾ ĐỘ</b>\n"
        f"{_tg_esc(msg)}\n"
        f"⚡ Đã TỪ CHỐI đặt lệnh để tránh tạo lệnh điều kiện ngoài ý muốn. "
        f"Vào cửa sổ Chrome bấm 'Lệnh thường' rồi bot chạy lại bình thường."
    )


# Cảnh báo khi chạm trần lỗ ngày — chỉ gửi 1 lần/ngày (không cần cooldown theo
# giây như session-dead, vì kill-switch chỉ "chạm" một lần rồi đứng yên tới
# hết ngày; so theo NGÀY để rerun app cùng ngày không gửi lại).
_last_loss_alert_day: str | None = None
_LOSS_ALERT_LOCK = threading.Lock()


def _alert_daily_loss_cap(loss_vnd: float, cap_vnd: float) -> None:
    global _last_loss_alert_day
    today = datetime.now().strftime("%Y-%m-%d")
    with _LOSS_ALERT_LOCK:
        if _last_loss_alert_day == today:
            return
        _last_loss_alert_day = today
    _tg_send(
        f"🛑 <b>#VN30F1M Auto-trade: CHẠM TRẦN LỖ NGÀY</b>\n"
        f"Lỗ ước tính hôm nay: {loss_vnd:,.0f}đ (trần {cap_vnd:,.0f}đ)\n"
        f"⚡ Đã TỰ ĐỘNG NGỪNG đặt lệnh mới cho tới hết ngày hôm nay. "
        f"Số ước tính lấy từ vị thế theo dõi của bot — có thể lệch với "
        f"PnL thật của tài khoản, kiểm tra lại trên VPS."
    )

# 1 điểm VN30F1M = 100.000đ/hợp đồng — PHẢI khớp _PT_VALUE_VND trong
# phaisinh_tab.py. Trùng lặp có chủ đích: import module đó ở đây (dù chỉ lấy
# 1 hằng số) rủi ro vòng lặp vì phaisinh_tab import auto_trader ngược lại;
# hằng số này cố định theo quy chế HNX, gần như không bao giờ đổi.
_PT_VALUE_VND = 100_000


def _today_realized_loss_vnd(qty: int) -> float:
    """Ước lượng lỗ THỰC HIỆN (đã đóng) hôm nay, quy ra VND.

    Lấy từ journal của vị thế ẢO mà engine luôn theo dõi (dù auto-trade bật
    hay tắt) — dùng làm PROXY cho hiệu quả thực tế, KHÔNG phải PnL thật của
    tài khoản VPS. Có thể lệch do trượt giá, giá khớp thật khác giá lý
    thuyết, hoặc một số lệnh không đặt được vì lỗi kỹ thuật/phiên chết. Đây
    là GIỚI HẠN AN TOÀN (kill-switch), không phải báo cáo kế toán.

    Import `load_trades` cục bộ trong hàm (không phải đầu file) — tránh vòng
    lặp: `daily_report.py` import `phaisinh_tab.py` ở cấp module, mà
    `phaisinh_tab.py` lại import `auto_trader.py` (module này) bên trong hàm.
    Import trễ đảm bảo `phaisinh_tab` đã nạp xong trước khi cần tới.
    """
    from datetime import date
    try:
        from .daily_report import load_trades
        trades = load_trades(day=date.today())
    except Exception:
        return 0.0
    if trades is None or trades.empty:
        return 0.0
    net_points = float(trades["net"].sum())
    return max(0.0, -net_points) * _PT_VALUE_VND * max(1, qty)


_DEFAULT_CFG = {
    "enabled": False,             # công tắc tổng — false thì mọi lệnh chỉ ghi log
    "dry_run": True,              # true: điền + chụp màn hình, KHÔNG bấm nút đặt
    "cdp_url": "http://127.0.0.1:9222",
    "page_url_contains": "vps.com.vn",
    "symbol": "VN30F1M",
    "max_qty": 1,
    "max_orders_per_day": 6,
    # true: cả tín hiệu THƯỜNG cũng tự bấm gửi (không chỉ điền sẵn) — mặc định
    # false vì nhóm thường chỉ +0,285đ/lệnh lịch sử, biên rất mỏng so phí 0,25đ.
    "auto_all_signals": False,
    # Trần lỗ THỰC HIỆN trong ngày (VND) — chạm/vượt thì TỪ CHỐI mọi lệnh auto
    # mới cho tới hết ngày, bất kể tín hiệu gì. 0 = tắt (không giới hạn).
    # Tính từ PnL vị thế ẢO trong journal (xem _today_realized_loss_vnd) —
    # đây là ƯỚC LƯỢNG, không phải PnL thật của tài khoản.
    "max_daily_loss_vnd": 0,
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


def _ticket_mode(page) -> tuple[str, str]:
    """('normal'|'condition'|'unknown', nhãn người đọc).

    Phiếu SmartPro có bộ chọn 'Lệnh thường' / 'Lệnh điều kiện' — cái đang chọn
    mang class 'select-active'. Nếu phiếu bị để ở 'Lệnh điều kiện / Stop Loss'
    (ví dụ sau khi bắt request SL/TP thủ công), cú bấm LONG/SHORT tạo LỆNH ĐIỀU
    KIỆN chứ không phải lệnh vào, và có thể bị VPS từ chối âm thầm. Phải kiểm
    TRƯỚC khi điền phiếu — xem sự cố lệnh ma ngày 03/09/2026.
    """
    try:
        r = page.evaluate(
            "() => {"
            " const n = document.querySelector('#select_normal_order');"
            " const c = document.querySelector('#select_condition_order');"
            " if (!n && !c) return {m:'unknown', l:'khong thay bo chon Lenh thuong/Lenh dieu kien'};"
            " const na = !!(n && n.classList.contains('select-active'));"
            " const ca = !!(c && c.classList.contains('select-active'));"
            " let sub = '';"
            " const s = document.querySelector('#right_stock_cd_code');"
            " if (s) sub = (s.innerText || '').trim();"
            " if (na && !ca) return {m:'normal', l:'Lenh thuong'};"
            " if (ca && !na) return {m:'condition', l:'Lenh dieu kien' + (sub ? ' / ' + sub : '')};"
            " return {m:'unknown', l:'khong xac dinh (normal=' + na + ' cond=' + ca + ')'};"
            "}"
        )
        return r.get("m", "unknown"), r.get("l", "")
    except Exception as e:
        return "unknown", f"loi doc che do phieu: {type(e).__name__}"


def _read_position(page) -> tuple[str, int]:
    """Đọc bảng vị thế phái sinh (table.tbl-status-danhmuc) → (chiều, số HĐ).

    chiều ∈ {'LONG','SHORT','NONE'}. Cột 'Vị thế' là số có dấu: '-1' = short 1,
    '1'/'+1' = long 1, '-' hoặc rỗng = phẳng.
    """
    try:
        raw = page.evaluate(
            "() => {"
            " const t = document.querySelector('table.tbl-status-danhmuc');"
            " if (!t) return null;"
            " for (const tr of t.querySelectorAll('tr')) {"
            "  const c = [...tr.querySelectorAll('td')].map(x => (x.innerText||'').trim());"
            "  if (c.length >= 2 && c[0] && c[0].length > 3) return c[1];"
            " }"
            " return null;"
            "}"
        )
    except Exception:
        return "NONE", 0
    if raw is None:
        return "NONE", 0
    raw = str(raw).replace(",", "").replace("+", "").strip()
    if raw in ("", "-", "0"):
        return "NONE", 0
    try:
        v = int(float(raw))
    except ValueError:
        return "NONE", 0
    return ("LONG", v) if v > 0 else ("SHORT", -v) if v < 0 else ("NONE", 0)


def _newest_order_sig(page) -> str:
    """Chữ ký (số hiệu | giờ) của lệnh mới nhất trong #order_normal — để phát
    hiện lệnh vừa vào. '' nếu chưa có lệnh nào / không đọc được."""
    try:
        return page.evaluate(
            "() => {"
            " const t = document.getElementById('order_normal');"
            " if (!t) return '';"
            " const rows = [...t.querySelectorAll('tr')];"
            " for (let i = 1; i < rows.length; i++) {"
            "  const c = [...rows[i].querySelectorAll('td')].map(x => (x.innerText||'').trim());"
            "  if (c.length && /\\d/.test(c[0])) return c[0] + '|' + (c[1] || '');"
            " }"
            " return '';"
            "}"
        ) or ""
    except Exception:
        return ""


def _read_error_popup(page) -> str | None:
    """Thông báo lỗi đang hiện (bootbox / toast-error) — KHÔNG tính hộp thoại
    'đăng nhập lại' (đã có `_session_alive` lo). None nếu không có."""
    try:
        return page.evaluate(
            "() => {"
            " const b = document.querySelector('.bootbox');"
            " if (b && (b.offsetWidth || b.offsetHeight)) {"
            "  const t = (b.innerText || '').trim();"
            "  if (t && !/dang nhap lai|đăng nhập lại/i.test(t)) return t.slice(0, 200);"
            " }"
            " const te = document.querySelector('.toast-error, .toast-danger, #toast-container .toast-error');"
            " if (te && (te.offsetWidth || te.offsetHeight)) return (te.innerText || '').trim().slice(0, 200);"
            " return null;"
            "}"
        )
    except Exception:
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


def _submit(page, cfg: dict, side: str) -> tuple[bool, str]:
    """Bấm nút LONG/SHORT (ở VPS = gửi lệnh) rồi XÁC NHẬN lệnh đã vào sàn.

    Trả (đã_xác_nhận, mô_tả). KHÔNG còn coi 'click không lỗi = thành công' —
    phải thấy lệnh mới trong #order_normal HOẶC vị thế đổi, hoặc đọc được thông
    báo lỗi rõ ràng. Hết 6s không có tín hiệu nào → coi là THẤT BẠI (caller
    không bump bộ đếm, không log '✅'). Xem sự cố lệnh ma ngày 03/09/2026:
    phiếu ở chế độ Stop Loss → bấm SHORT không lỗi nhưng lệnh không tới VPS,
    bot vẫn log '✅ ĐÃ ĐẶT' và tăng bộ đếm.
    """
    sel = cfg["selectors"]
    side_btn = sel.get("long_button") if side == "LONG" else sel.get("short_button")
    if not side_btn:
        return False, f"Chưa cấu hình selector nút {side}"

    sig0 = _newest_order_sig(page)
    pos0 = _read_position(page)
    try:
        page.click(side_btn, timeout=4000)
    except Exception as e:
        return False, f"Bấm nút {side} lỗi: {type(e).__name__}: {str(e)[:120]}"

    # Modal "Xác nhận trước khi đặt lệnh" (nếu tài khoản bật) — bấm nếu nó hiện.
    # Không bật thì bỏ qua; chốt cuối là bước xác minh kết quả bên dưới.
    cbtn = sel.get("confirm_button")
    if cbtn:
        try:
            page.wait_for_selector(cbtn, state="visible", timeout=1500)
            page.click(cbtn, timeout=2500)
        except Exception:
            pass

    deadline = time.time() + 6.0
    while time.time() < deadline:
        err = _read_error_popup(page)
        if err:
            return False, f"VPS báo lỗi: {err}"
        if _newest_order_sig(page) not in ("", sig0):
            return True, "thấy lệnh mới trong sổ lệnh"
        pos_now = _read_position(page)
        if pos_now != pos0:
            return True, f"vị thế đổi {pos0[0]}x{pos0[1]} → {pos_now[0]}x{pos_now[1]}"
        time.sleep(0.4)
    return False, ("KHÔNG xác nhận được lệnh đã vào sàn sau 6s (sổ lệnh không có "
                   "lệnh mới, vị thế không đổi) — kiểm tra VPS thủ công")


# ── API chính ────────────────────────────────────────────────────────────────

def _place_sltp(page, cfg: dict, position_side: str, qty: int,
                cur_price: float | None, sl_price: float | None,
                tp_price: float | None) -> tuple[bool, str]:
    """Đặt Stop Loss / Take Profit THẬT trên sàn (lệnh điều kiện) ngay sau khi
    lệnh vào vừa khớp — khớp tự động tại VPS, không phụ thuộc bot còn chạy
    (tắt máy, mất mạng, Streamlit crash... vị thế thật vẫn được sàn bảo vệ).

    Định dạng request xác nhận SỐNG 03/09/2026: bắt lưu lượng mạng thật lúc
    user tự đặt SL/TP qua giao diện (lệnh SHORT thật, VPS xác nhận "chờ
    khớp"). Gửi lại request đó bằng `fetch()` NGAY TRONG trang (không phải
    từ Python) — cookie phiên (`ASP.NET_SessionId`) đặt cờ HttpOnly, không
    đọc được từ ngoài, nhưng `fetch()` cùng-origin từ trong trang tự động
    kèm cookie đó mà không cần biết giá trị.

    `session`/`user` đọc SỐNG từ `global.sid`/`global.user`. `extInfo` đọc
    SỐNG từ `window.Fingerprint` — biến toàn cục VPS tự tính sẵn, đúng định
    dạng "<số thiết bị>|<user agent>" (khớp y hệt request thật đã bắt được).
    Đã thử `FingerprintJS.load()` cho ra visitorId KHÁC — không cùng cơ chế,
    không dùng.

    `position_side`: chiều VỊ THẾ vừa mở ("LONG"/"SHORT") — payload `side`
    của VPS là chiều VỊ THẾ (không phải chiều lệnh), xác nhận qua thao tác
    thật của user ("tôi đặt short nhé" → side='S').

    `tp_price=None` → gửi TP rỗng (chỉ đặt SL sàn) — v4 mặc định KHÔNG có
    take-profit (xem CLAUDE.md, quyết định có nghiên cứu hậu thuẫn). Hàm này
    chỉ MIRROR đúng vị thế ảo (`pos["sl"]`/`pos.get("tp")` từ điểm gọi),
    không tự quyết định chính sách TP.

    Trả (đã_gửi_được_qua_mạng, response_thô) — KHÔNG tự khẳng định VPS đã
    CHẤP NHẬN lệnh (chưa bắt được mẫu response lỗi thật để biết chắc schema
    báo lỗi) — response thô luôn được log/trả về để xác minh bằng mắt, giống
    cách `_submit()`/`close_position()` đã làm (log + chụp màn hình).
    """
    vps_side = "S" if position_side == "SHORT" else "B"
    sl_s = f"{sl_price:.1f}" if sl_price is not None else "0"
    tp_s = f"{tp_price:.1f}" if tp_price is not None else "0"
    price_s = f"{cur_price:.1f}" if cur_price else ""
    try:
        result = page.evaluate(
            """
            async ([sym, side, qty, price, sl, tp]) => {
                try {
                    const acctEl = document.getElementById('right_account');
                    const acct = acctEl ? acctEl.value : null;
                    const sid = (typeof global !== 'undefined') ? global.sid : null;
                    const usr = (typeof global !== 'undefined') ? global.user : null;
                    if (!acct || !sid || !usr) {
                        return {sent:false, err:'khong doc duoc accountNo/session/user song'};
                    }
                    const body = {
                        group: 'O', session: sid, user: usr,
                        extInfo: window.Fingerprint || '', language: 'vi',
                        data: {
                            cmd: 'co.sltp.order.new', accountNo: acct, pin: '',
                            channel: 'H', placedPrice: price, priceType: 'LO',
                            side: side, quantity: qty, stopOrderType: 'sl_tp',
                            symbol: sym, spread: '', triggerType: 'price',
                            stopLossPrice: sl, takeProfitPrice: tp,
                            stopLossAmount: '0', takeProfitAmount: '0',
                        },
                    };
                    const resp = await fetch('/handler/core_ext.vpbs', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body),
                        credentials: 'same-origin',
                    });
                    const text = await resp.text();
                    return {sent: true, http_ok: resp.ok, status: resp.status,
                            text: text.slice(0, 500)};
                } catch (e) {
                    return {sent:false, err: String(e)};
                }
            }
            """,
            [cfg["symbol_code"], vps_side, str(qty), price_s, sl_s, tp_s],
        )
    except Exception as e:
        return False, f"Lỗi gọi trang: {type(e).__name__}: {str(e)[:150]}"

    if not result or not result.get("sent"):
        err = (result or {}).get("err", "không rõ lỗi")
        return False, f"KHÔNG gửi được request: {err}"

    txt = str(result.get("text") or "")
    # rc:0 + HTTP 200 KHÔNG có nghĩa VPS chấp nhận: body vẫn có thể mang mã lỗi
    # FOS-xxxx (03/09/2026 gặp FOS-6012 'Vượt quá khối lượng có thể mua' cả 3
    # lần mà hàm này vẫn trả True). Coi mọi FOS-<số> trong response là TỪ CHỐI.
    import re as _re
    mcode = _re.search(r'"code"\s*:\s*"(FOS-[^"]+)"', txt)
    if mcode or _re.search(r'FOS-\d', txt):
        code = mcode.group(1) if mcode else "FOS-?"
        return False, f"VPS TỪ CHỐI lệnh SL/TP (mã {code}) — {txt[:200]}"
    tp_note = tp_s if tp_price is not None else "(không đặt — v4 không TP mặc định)"
    return True, (f"SL={sl_s} TP={tp_note} — HTTP {result.get('status')} — "
                  f"phản hồi: {txt[:200]}")


def submit_signal(side: str, strong: bool, price: float | None = None,
                  in_session: bool = True, sl_price: float | None = None,
                  tp_price: float | None = None) -> tuple[bool, str]:
    """Xử lý 1 tín hiệu theo chính sách: MẠNH (hoặc mọi tín hiệu nếu bật
    `auto_all_signals`) → đặt tự động; còn lại → chỉ điền sẵn.

    Trả (đã_thao_tác_browser, mô_tả). Mọi nhánh đều ghi log.
    """
    cfg = load_config()
    tag = "⭐MẠNH" if strong else "thường"
    full_submit = strong or bool(cfg.get("auto_all_signals"))

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

    # Trần lỗ trong ngày — chỉ chặn khi SẮP TỰ GỬI THẬT (full_submit); lệnh chỉ
    # điền sẵn không tự bấm nên không cần chặn (người dùng vẫn tự quyết được).
    _cap = int(cfg.get("max_daily_loss_vnd", 0) or 0)
    if full_submit and _cap > 0:
        _loss = _today_realized_loss_vnd(qty)
        if _loss >= _cap:
            _log(f"⛔ {side} ({tag}) — CHẠM TRẦN LỖ NGÀY: đã lỗ ước tính "
                f"{_loss:,.0f}đ ≥ trần {_cap:,.0f}đ — từ chối mọi lệnh auto "
                f"tới hết ngày")
            _alert_daily_loss_cap(_loss, _cap)
            return False, f"Chạm trần lỗ ngày ({_loss:,.0f}đ ≥ {_cap:,.0f}đ)"

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

                mode, mlabel = _ticket_mode(page)
                if mode != "normal":
                    shot = _shot(page, "ticket_mode")
                    msg = (f"Phiếu lệnh VPS đang ở chế độ '{mlabel}', không phải "
                           f"'Lệnh thường' — TỪ CHỐI đặt lệnh (tránh tạo lệnh điều "
                           f"kiện ngoài ý muốn). Bấm 'Lệnh thường' trong cửa sổ Chrome.")
                    _log(f"⛔ {side} ({tag}) — {msg} · ảnh {shot}")
                    _alert_ticket_mode(msg)
                    browser.close()
                    return False, msg

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

                # Không thuộc diện tự gửi (không MẠNH và auto_all_signals tắt):
                # dừng ở điền sẵn — người dùng tự bấm.
                if not full_submit:
                    shot = _shot(page, "prefill")
                    _log(f"📝 ĐIỀN SẴN {side} x{qty} ({tag}) — chờ người dùng bấm "
                         f"· ảnh {shot}")
                    browser.close()
                    return True, f"Đã điền sẵn {side} x{qty} — bạn bấm xác nhận"

                # Thuộc diện tự gửi: đặt tự động (trừ khi dry-run)
                if cfg.get("dry_run", True):
                    shot = _shot(page, "dryrun")
                    _log(f"🧪 DRY-RUN {side} x{qty} ({tag}) — đã điền, KHÔNG bấm "
                         f"· ảnh {shot}")
                    browser.close()
                    return True, f"DRY-RUN: đã điền {side} x{qty}, không bấm"

                ok_sub, sub_msg = _submit(page, cfg, side)
                if not ok_sub:
                    shot = _shot(page, "submit_err")
                    _log(f"⚠️ {side} ({tag}) — KHÔNG XÁC NHẬN lệnh vào sàn: {sub_msg} "
                         f"· ảnh {shot} — bộ đếm KHÔNG tăng, kiểm tra VPS.")
                    browser.close()
                    return False, sub_msg

                _bump_orders_today()
                shot = _shot(page, "submitted")
                _log(f"✅ ĐÃ ĐẶT {side} x{qty} ({tag}, lệnh thứ {_orders_today()} "
                     f"hôm nay — {sub_msg}) · ảnh {shot}")

                # Đặt SL/TP sàn ngay sau khi lệnh vào vừa khớp — mirror đúng
                # SL/TP của vị thế ảo (sl_price/tp_price truyền từ điểm gọi).
                # Lỗi ở bước này KHÔNG được coi là lỗi mở lệnh (vị thế THẬT đã
                # mở) — chỉ log cảnh báo riêng, người dùng tự đặt SL tay nếu cần.
                # CHỈ gửi khi ĐỌC ĐƯỢC vị thế thật đúng chiều — tránh lệnh điều
                # kiện trần trụi (order 83020 ngày 03/09 tự mở vị thế, lỗ -1,34tr).
                sltp_note = ""
                if sl_price is not None:
                    pos_side, pos_qty = _read_position(page)
                    if pos_side != side:
                        _log(f"⚠️ SL/TP {side} — BỎ QUA: bảng vị thế đọc được "
                             f"'{pos_side} x{pos_qty}' ≠ chiều vừa đặt ({side}). "
                             f"Không gửi lệnh điều kiện khi chưa chắc có vị thế nền.")
                        sltp_note = " — chưa đặt SL/TP sàn (chưa xác nhận được vị thế)"
                    else:
                        ok_sltp, msg_sltp = _place_sltp(page, cfg, side, qty, price,
                                                         sl_price, tp_price)
                        shot2 = _shot(page, "sltp")
                        if ok_sltp:
                            _log(f"🛡️ SL/TP {side} — {msg_sltp} · ảnh {shot2}")
                            sltp_note = " + đã gửi SL/TP sàn (kiểm tra lại trên VPS)"
                        else:
                            _log(f"⚠️ SL/TP {side} — KHÔNG gửi được: {msg_sltp} · ảnh {shot2} — "
                                 f"vị thế THẬT vẫn mở nhưng KHÔNG có SL sàn, cân nhắc đặt tay.")
                            sltp_note = " nhưng KHÔNG đặt được SL/TP sàn — cân nhắc đặt tay"

                browser.close()
                return True, f"ĐÃ ĐẶT {side} x{qty}{sltp_note}"
        except Exception as e:
            _log(f"❌ {side} ({tag}) — {type(e).__name__}: {str(e)[:150]}")
            return False, f"{type(e).__name__}: {str(e)[:80]}"


def submit_signal_async(side: str, strong: bool, price: float | None = None,
                        in_session: bool = True, sl_price: float | None = None,
                        tp_price: float | None = None) -> None:
    """Bản chạy nền — gọi từ engine, không chặn render (như _send_telegram_async)."""
    threading.Thread(target=submit_signal,
                     args=(side, strong, price, in_session, sl_price, tp_price),
                     daemon=True).start()


def _prefill_close(page, cfg: dict, position_side: str, qty: int) -> str | None:
    """Gọi thẳng hàm `ClosePosition()` THẬT của VPS để điền đúng phiếu đóng.

    Phát hiện 03/09/2026 bằng cách đọc `ClosePosition.toString()` từ trình
    duyệt đang chạy: click vào số vị thế (ô "-1"/"+1" trong bảng Tài sản) gọi
    `ClosePosition(symbol, type, vol, true)` — hàm này KHÔNG tự gửi lệnh, chỉ
    điền `#right_stock_cd`/`#right_price`/`#sohopdong` (dùng giá thị trường
    tra từ `reciprocalStock`/`multiStock`, không phải giá limit tay) và BẬT
    đúng nút chiều lệnh cần bấm — kiến trúc giống hệt `_fill_ticket()` đã xây
    cho lệnh mở. Dùng lại chính hàm JS của VPS thay vì tự mô phỏng tra giá
    market — đảm bảo hành vi giống hệt một cú click thật của người dùng.

    `position_side`: chiều VỊ THẾ ĐANG GIỮ ("LONG"/"SHORT") — hàm tự suy ra
    `type` cho VPS (chiều LỆNH cần đặt để đóng, ngược lại vị thế):
      đang giữ SHORT → cần MUA để đóng → type='B' → nút LONG được bật
      đang giữ LONG  → cần BÁN để đóng → type='S' → nút SHORT được bật
    """
    vps_type = "B" if position_side == "SHORT" else "S"
    try:
        ok = page.evaluate(
            "([sym, typ, vol]) => { "
            "if (typeof ClosePosition !== 'function') return false; "
            "ClosePosition(sym, typ, String(vol), true); return true; }",
            [cfg["symbol_code"], vps_type, qty],
        )
    except Exception as e:
        return f"Gọi ClosePosition() lỗi: {type(e).__name__}: {str(e)[:120]}"
    if not ok:
        return ("Không thấy hàm ClosePosition() trên trang — VPS có thể đã đổi "
                "giao diện, chạy lại dò DOM trước khi tin cơ chế này")
    return None


def close_position(position_side: str, qty: int = 1,
                   price: float | None = None) -> tuple[bool, str]:
    """Đóng vị thế THẬT trên VPS — dùng khi engine ảo quyết định thoát lệnh.

    `position_side`: chiều VỊ THẾ ĐANG GIỮ (không phải chiều lệnh sẽ đặt để
    đóng — hàm tự suy ra qua `_prefill_close`). Trả (đã_thao_tác_browser, mô_tả).

    Cố ý KHÔNG áp `max_orders_per_day` / `max_daily_loss_vnd` — hai trần đó
    giới hạn RỦI RO MỚI (mở thêm vị thế), còn đóng lệnh làm GIẢM rủi ro đang
    có. Chặn đóng vì "chạm trần" sẽ giữ một vị thế thua lỗ mở mãi — đúng thứ
    trần lỗ được sinh ra để tránh.

    Dùng chung `dry_run`/`enabled` với `submit_signal()` vì đây là một phần
    của CÙNG vị thế mà auto-trade đã mở — không có công tắc tách riêng.
    """
    cfg = load_config()
    close_side = "SHORT" if position_side == "LONG" else "LONG"   # chiều LỆNH

    if not cfg.get("enabled"):
        _log(f"⏸️ ĐÓNG {position_side} — auto trade đang TẮT. "
            f"⚠️ VỊ THẾ THẬT VẪN CÒN MỞ, cần tự đóng tay trên VPS.")
        return False, "Auto trade đang tắt — vị thế thật CHƯA được đóng tự động"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("❌ Thiếu playwright — vị thế thật vẫn còn mở")
        return False, "Thiếu playwright"
    _ensure_win_proactor_policy()

    with _LOCK:
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(cfg["cdp_url"], timeout=5000)
                page = _find_vps_page(browser, cfg["page_url_contains"])
                if page is None:
                    _log(f"❌ ĐÓNG {position_side} — không thấy tab VPS. "
                        f"⚠️ VỊ THẾ THẬT VẪN CÒN MỞ.")
                    browser.close()
                    return False, "Không thấy tab VPS — vị thế thật vẫn còn mở"

                alive, sess_msg = _session_alive(page)
                if not alive:
                    shot = _shot(page, "close_session_dead")
                    _log(f"⛔ ĐÓNG {position_side} — {sess_msg} · ảnh {shot} — "
                        f"⚠️ VỊ THẾ THẬT VẪN CÒN MỞ, cần tự đóng tay.")
                    _alert_session_dead(
                        f"KHÔNG ĐÓNG ĐƯỢC vị thế {position_side} đang mở — {sess_msg} "
                        f"— vị thế thật vẫn còn mở, cần tự đóng tay trên VPS."
                    )
                    browser.close()
                    return False, sess_msg

                mode, mlabel = _ticket_mode(page)
                if mode != "normal":
                    shot = _shot(page, "close_ticket_mode")
                    msg = (f"Phiếu lệnh VPS đang ở chế độ '{mlabel}', không phải "
                           f"'Lệnh thường' — KHÔNG đóng tự động. ⚠️ VỊ THẾ THẬT VẪN "
                           f"CÒN MỞ, bấm 'Lệnh thường' rồi đóng tay/để bot thử lại.")
                    _log(f"⛔ ĐÓNG {position_side} — {msg} · ảnh {shot}")
                    _alert_ticket_mode(msg)
                    browser.close()
                    return False, msg

                # Đọc vị thế THẬT trước khi gửi lệnh đóng — đóng bằng cách đặt lệnh
                # ngược chiều, nên nếu VPS không thực sự có vị thế đúng chiều thì
                # lệnh "đóng" sẽ MỞ một vị thế ngược trần trụi (sự cố order 184790
                # ngày 03/09: engine đóng SHORT ảo trong khi TK thật phẳng → nằm
                # LONG 1 qua đêm).
                pos_side, pos_qty = _read_position(page)
                if pos_side != position_side:
                    shot = _shot(page, "close_no_position")
                    if pos_side == "NONE":
                        msg = ("VPS KHÔNG có vị thế nào đang mở — KHÔNG gửi lệnh "
                               "đóng (tránh mở vị thế ngược trần trụi). Kiểm tra "
                               "tay nếu bạn tin có vị thế thật.")
                    else:
                        msg = (f"Vị thế thật trên VPS là '{pos_side} x{pos_qty}', "
                               f"khác chiều engine muốn đóng ('{position_side}') — "
                               f"KHÔNG tự đóng, kiểm tra tay.")
                    _log(f"⛔ ĐÓNG {position_side} — {msg} · ảnh {shot}")
                    browser.close()
                    return False, msg

                err = _verify_symbol_price(page, cfg, price)
                if err:
                    shot = _shot(page, "close_stale_symbol")
                    _log(f"⛔ ĐÓNG {position_side} — {err} · ảnh {shot} — "
                        f"⚠️ VỊ THẾ THẬT VẪN CÒN MỞ.")
                    browser.close()
                    return False, err

                err = _prefill_close(page, cfg, position_side, qty)
                if err:
                    shot = _shot(page, "close_fill_err")
                    _log(f"❌ ĐÓNG {position_side} — {err} · ảnh {shot} — "
                        f"⚠️ VỊ THẾ THẬT VẪN CÒN MỞ.")
                    browser.close()
                    return False, err

                if cfg.get("dry_run", True):
                    shot = _shot(page, "close_dryrun")
                    _log(f"🧪 DRY-RUN ĐÓNG {position_side} x{qty} — đã điền, "
                        f"KHÔNG bấm · ảnh {shot}")
                    browser.close()
                    return True, f"DRY-RUN: đã điền lệnh đóng {position_side} x{qty}"

                ok_sub, sub_msg = _submit(page, cfg, close_side)
                if not ok_sub:
                    shot = _shot(page, "close_submit_err")
                    _log(f"⚠️ ĐÓNG {position_side} — KHÔNG XÁC NHẬN lệnh đóng vào "
                        f"sàn: {sub_msg} · ảnh {shot} — ⚠️ VỊ THẾ THẬT CÓ THỂ VẪN "
                        f"CÒN MỞ, kiểm tra VPS + đóng tay.")
                    browser.close()
                    return False, sub_msg

                shot = _shot(page, "close_submitted")
                _log(f"✅ ĐÃ ĐÓNG vị thế {position_side} x{qty} "
                    f"(lệnh {close_side} — {sub_msg}) · ảnh {shot}")
                browser.close()
                return True, f"ĐÃ ĐÓNG {position_side} x{qty}"
        except Exception as e:
            _log(f"❌ ĐÓNG {position_side} — {type(e).__name__}: {str(e)[:150]} — "
                f"⚠️ VỊ THẾ THẬT VẪN CÒN MỞ, cần tự đóng tay.")
            return False, f"{type(e).__name__}: {str(e)[:80]}"


def close_position_async(position_side: str, qty: int = 1,
                         price: float | None = None) -> None:
    """Bản chạy nền — gọi từ engine, không chặn render."""
    threading.Thread(target=close_position,
                     args=(position_side, qty, price), daemon=True).start()
