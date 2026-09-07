"""AutoTrade VPS qua phiên Chrome đã đăng nhập (CDP).

Gửi thật dùng autotrade_runtime và vps_broker: một lệnh mở gắn SL/TP,
đối soát sổ lệnh/vị thế/PnL phía server và lưu trạng thái qua restart.
Tín hiệu thường được gửi khi auto_all_signals bật; tín hiệu mạnh dùng 1 HĐ.
Dry-run/điền sẵn vẫn dùng phiếu DOM. Người dùng tự đăng nhập và xác thực VPS.
Module không import Streamlit; worker quản lý riêng vòng đời vị thế thật.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

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
    "normal_stop_enabled": True, # bảo vệ vị thế lệnh thường đã khớp khi gửi thật bật
    "telegram_vps_reports": True, # báo cáo sổ lệnh/PnL thật, độc lập bật/tắt đặt lệnh
    "submit_timeout_seconds": 20, # allow delayed order-book updates; never retry submission
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
    from .autotrade_runtime import status
    return status()['attempts']


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
    # Never choose a stale tab or a different account merely by tab order.
    candidates = [page for ctx in browser.contexts for page in ctx.pages
                  if urlparse(page.url or "").hostname == "smartpro.vps.com.vn"
                  and urlparse(page.url or "").path.startswith("/v1/")
                  and url_part in (page.url or "")]
    alive = [page for page in candidates if _session_alive(page)[0]]
    if len(alive) > 1:
        raise ValueError("Có nhiều tab SmartPro còn phiên — chỉ giữ một tab giao dịch "
                         "trong Chrome AutoTrade để tránh chọn nhầm tài khoản")
    return alive[0] if alive else (candidates[0] if candidates else None)


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
    except ValueError as e:
        return False, str(e)
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

    Đây chỉ là kiểm tra DOM, không chứng minh phiên phía server/PIN còn hợp lệ.
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
            "return !!(b && (b.offsetWidth || b.offsetHeight) && b.innerText && "
            "b.innerText.includes('đăng nhập lại')); }"
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
    if not cfg.get("dry_run", True):
        try:
            from .vps_broker import connect
            with connect(cfg) as broker:
                broker.snapshot()
            return True, "Đã xác minh phiên server, tài khoản, vị thế và sổ lệnh VPS; chưa gửi lệnh thử"
        except Exception as exc:
            return False, f"Không xác minh được phiên VPS: {type(exc).__name__}: {str(exc)[:100]}"
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
            if alive:
                mode, label = _ticket_mode(page)
                if mode != "normal":
                    alive = False
                    sess_msg = (f"Phiên còn giao diện nhưng phiếu đang ở '{label}'. "
                                "Chọn Lệnh thường trên SmartPro rồi kiểm tra lại.")
            browser.close()
            return alive, ("Giao diện VPS ở Lệnh thường; chưa xác minh phiên phía server "
                           "hoặc PIN và chưa gửi lệnh thử" if alive else sess_msg)
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


def _read_position(page, symbol: str | None = None) -> tuple[str, int]:
    """Đọc bảng vị thế phái sinh (table.tbl-status-danhmuc) → (chiều, số HĐ).

    chiều ∈ {'LONG','SHORT','NONE','UNKNOWN'}. Cột 'Vị thế' là số có dấu: '-1' = short 1,
    '1'/'+1' = long 1, '-' hoặc rỗng = phẳng.
    """
    try:
        raw = page.evaluate(
            "(symbol) => {"
            " const t = document.querySelector('table.tbl-status-danhmuc');"
            " if (!t) return null;"
            " for (const tr of t.querySelectorAll('tr')) {"
            "  const c = [...tr.querySelectorAll('td')].map(x => (x.innerText||'').trim());"
            "  if (c.length >= 2 && c[0] && c[0].length > 3 "
            "      && (!symbol || c[0] === symbol)) return c[1];"
            " }"
            " return null;"
            "}", symbol
        )
    except Exception:
        return "UNKNOWN", 0
    if raw is None:
        return "UNKNOWN", 0
    raw = str(raw).replace(",", "").replace("+", "").strip()
    if raw in ("", "-", "0"):
        return "NONE", 0
    try:
        number = float(raw)
        if not math.isfinite(number) or not number.is_integer():
            return "UNKNOWN", 0
        v = int(number)
    except ValueError:
        return "UNKNOWN", 0
    return ("LONG", v) if v > 0 else ("SHORT", -v) if v < 0 else ("NONE", 0)


def _order_snapshot(page, expected: dict) -> list[dict]:
    """Read only identifiers/statuses; keep account details out of logs."""
    return page.evaluate(
        """expected => {
            const table = document.getElementById('order_normal');
            if (!table) throw new Error('Khong doc duoc so lenh');
            const number = text => Number(String(text).replace(/,/g, '').trim());
            return [...table.querySelectorAll('tr')].map(row => {
                const c = [...row.querySelectorAll('td')].map(x => (x.innerText || '').trim());
                if (c.length < 11 || !/^\\d+$/.test(c[0])) return null;
                return {id:c[0], status:c[10], matches:
                    c[2] === expected.account && c[3].toUpperCase() === expected.side &&
                    c[4] === expected.symbol && number(c[5]) === expected.qty &&
                    Math.abs(number(c[7]) - expected.price) < 0.001};
            }).filter(Boolean);
        }""", expected)


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
    """Submit once and reconcile a new matching order, never infer a fill from a click.

    A timeout is UNKNOWN, not a rejection: never automatically retry it.
    """
    sel = cfg["selectors"]
    side_btn = sel.get("long_button") if side == "LONG" else sel.get("short_button")
    if not side_btn:
        return False, f"Chưa cấu hình selector nút {side}"
    try:
        expected = page.evaluate(
            """([sel, side]) => {
                const value = selector => document.querySelector(selector)?.value;
                return {account:value('#right_account'), symbol:value(sel.symbol_select),
                        side, qty:Number(value(sel.qty_input)),
                        price:Number(String(value(sel.price_input)).replace(/,/g, ''))};
            }""", [sel, side])
        if (not expected or not expected.get("account")
                or expected.get("symbol") != cfg["symbol_code"]
                or not isinstance(expected.get("qty"), (int, float))
                or not math.isfinite(expected["qty"]) or expected["qty"] <= 0
                or expected["qty"] != int(expected["qty"])
                or not isinstance(expected.get("price"), (int, float))
                or not math.isfinite(expected["price"]) or expected["price"] <= 0):
            return False, "Không đọc được phiếu LO hợp lệ để đối chiếu — chưa bấm gửi"
        before = {row["id"] for row in _order_snapshot(page, expected)}
    except Exception:
        return False, "Không đọc được phiếu/sổ lệnh trước khi gửi — chưa bấm gửi"

    try:
        page.click(side_btn, timeout=4000)
    except Exception as e:
        return False, f"Bấm nút {side} lỗi: {type(e).__name__} — kiểm tra VPS trước khi thử lại"

    cbtn = sel.get("confirm_button")
    confirmed = False
    if cbtn:
        try:
            page.wait_for_selector(cbtn, state="visible", timeout=1500)
            page.click(cbtn, timeout=2500)
            confirmed = True
        except Exception:
            # Modal may be disabled or absent; reconcile the order book either way.
            pass

    try:
        wait_seconds = float(cfg.get("submit_timeout_seconds", 20))
        if not math.isfinite(wait_seconds):
            wait_seconds = 20.0
        wait_seconds = max(6.0, min(wait_seconds, 60.0))
    except (TypeError, ValueError):
        wait_seconds = 20.0
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        # The confirmation dialog may arrive after the initial 1.5-second wait.
        if cbtn and not confirmed:
            try:
                page.wait_for_selector(cbtn, state="visible", timeout=100)
                page.click(cbtn, timeout=500)
                confirmed = True
            except Exception:
                pass
        err = _read_error_popup(page)
        if err:
            return False, f"VPS báo thông báo cần kiểm tra: {err}"
        try:
            rows = _order_snapshot(page, expected)
        except Exception:
            rows = []
        for row in rows:
            if row["id"] in before or not row.get("matches"):
                continue
            status = row.get("status", "").strip().casefold()
            if status in ("từ chối", "đã hủy", "hết hiệu lực"):
                return False, f"Lệnh khớp phiếu có trạng thái: {row['status']}"
            if status in ("đã khớp", "khớp một phần", "khớp 1 phần", "chờ khớp", "chờ xử lý"):
                return True, f"VPS ghi nhận lệnh đúng tài khoản/mã/chiều/KL/giá: {row['status']}"
        time.sleep(0.4)
    return False, (f"CHƯA RÕ kết quả sau {wait_seconds:g}s — chưa thấy lệnh mới khớp phiếu. "
                   "Không tự gửi lại; đối chiếu sổ lệnh VPS trước khi thử lại")


# ── API chính ────────────────────────────────────────────────────────────────

def _place_sltp(*args, **kwargs):
    """Never call co.sltp.order.new after opening: it creates another parent order."""
    return False, "SL/TP phải gắn vào lệnh mở ban đầu; không gửi thêm lệnh mở"


def submit_signal(side: str, strong: bool, price: float | None = None,
                  in_session: bool = True, sl_price: float | None = None,
                  tp_price: float | None = None, signal_id: str | None = None,
                  signal_at: str | None = None) -> tuple[bool, str]:
    """Xử lý 1 tín hiệu theo chính sách: MẠNH (hoặc mọi tín hiệu nếu bật
    `auto_all_signals`) → đặt tự động; còn lại → chỉ điền sẵn.

    Trả (đã_thao_tác_browser, mô_tả). Mọi nhánh đều ghi log.
    """
    cfg = load_config()
    if side not in ("LONG", "SHORT"):
        return False, "Chiều lệnh phải là LONG hoặc SHORT"
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float))
                              or not math.isfinite(price) or price <= 0):
        return False, "Giá lệnh phải hữu hạn và lớn hơn 0"
    tag = "⭐MẠNH" if strong else "thường"
    full_submit = strong or bool(cfg.get("auto_all_signals"))
    if cfg.get("enabled") and full_submit and not cfg.get("dry_run", True):
        from .autotrade_runtime import submit
        return submit(side, strong, price, in_session, sl_price, tp_price, signal_id, signal_at)

    if not cfg.get("enabled"):
        _log(f"⏸️ {side} ({tag}) — auto trade đang TẮT, bỏ qua")
        return False, "Auto trade đang tắt"
    if not in_session:
        _log(f"⛔ {side} ({tag}) — ngoài giờ giao dịch, bỏ qua")
        return False, "Ngoài giờ giao dịch"
    if _orders_today() >= int(cfg.get("max_orders_per_day", 6)):
        _log(f"⛔ {side} ({tag}) — chạm trần {cfg['max_orders_per_day']} lệnh/ngày")
        return False, "Chạm trần lệnh/ngày"

    max_qty = cfg.get("max_qty", 1)
    if type(max_qty) is not int or max_qty <= 0:
        return False, "max_qty phải là số nguyên dương — chưa gửi lệnh"
    qty = 1 if strong else max_qty

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("❌ Thiếu playwright")
        return False, "Thiếu playwright"
    _ensure_win_proactor_policy()

    with _LOCK:
        # Recheck after waiting for another order; the earlier count may be stale.
        if _orders_today() >= int(cfg.get("max_orders_per_day", 6)):
            return False, "Chạm trần lệnh/ngày"
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

        except Exception as e:
            _log(f"❌ {side} ({tag}) — {type(e).__name__}: {str(e)[:150]}")
            return False, f"{type(e).__name__}: {str(e)[:80]}"


def submit_signal_async(side: str, strong: bool, price: float | None = None,
                        in_session: bool = True, sl_price: float | None = None,
                        tp_price: float | None = None, signal_id: str | None = None,
                        signal_at: str | None = None) -> None:
    def run():
        ok, msg = submit_signal(side, strong, price, in_session, sl_price, tp_price,
                                signal_id, signal_at)
        _log(f"AutoTrade {side}: {msg}")
    threading.Thread(target=run, daemon=True).start()


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
                   price: float | None = None, signal_id: str | None = None) -> tuple[bool, str]:
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
    if position_side not in ("LONG", "SHORT") or type(qty) is not int or qty <= 0:
        return False, "Chiều vị thế hoặc khối lượng đóng không hợp lệ"
    if cfg.get("enabled") and not cfg.get("dry_run", True):
        from .autotrade_runtime import request_close
        return request_close(position_side, qty, price, signal_id)
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
                pos_side, pos_qty = _read_position(page, cfg["symbol_code"])
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

                if qty > pos_qty:
                    browser.close()
                    return False, (f"Khối lượng đóng {qty} vượt vị thế thật {pos_qty} — "
                                   "không gửi để tránh mở vị thế ngược")

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

        except Exception as e:
            _log(f"❌ ĐÓNG {position_side} — {type(e).__name__}: {str(e)[:150]} — "
                f"⚠️ VỊ THẾ THẬT VẪN CÒN MỞ, cần tự đóng tay.")
            return False, f"{type(e).__name__}: {str(e)[:80]}"


def close_position_async(position_side: str, qty: int = 1,
                         price: float | None = None, signal_id: str | None = None) -> None:
    def run():
        ok, msg = close_position(position_side, qty, price, signal_id)
        _log(f"AutoTrade đóng {position_side}: {msg}")
    threading.Thread(target=run, daemon=True).start()
