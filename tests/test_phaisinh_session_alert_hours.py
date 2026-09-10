"""Cảnh báo "phiên VPS có vấn đề" KHÔNG được bắn ngoài giờ giao dịch.

Lỗi thật 09/09/2026: người dùng nhận tin Telegram báo không kết nối được lúc
~20h. Khối kiểm tra phiên VPS trong `_live_panel_body()` chạy mỗi khi thấy "nến
mới", mà mốc so sánh `ps_last_time` nằm trong `session_state` — RIÊNG từng phiên
trình duyệt. Mở/F5 tab buổi tối ⇒ `ps_last_time` rỗng ⇒ nến 14:45 của phiên
chiều bị coi là mới ⇒ check chạy ⇒ Chrome đã tắt từ lâu ⇒ Telegram giữa đêm.

Test render THẬT tab Phái Sinh qua `AppTest` (không đọc source) nên vẫn bắt được
lỗi nếu sau này khối kiểm tra bị dời sang chỗ khác trong hàm.

⚠️ Cùng lý do như `test_phaisinh_render.py`: render gọi `_live_panel_body()`,
hàm này đụng worker/journal/Chrome THẬT nếu không bịt trước. Ở đây bắt buộc
`enabled=True` (không có nó thì khối cần kiểm không chạy), nên phải bịt thêm
`submit_signal_async`/`close_position_async` để không có đường nào chạm Chrome.
"""
import os
from datetime import date

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_feed(path):
    """300 nến 1 phút của HÔM NAY, nến cuối 14:45 — đúng trạng thái sau phiên."""
    today = date.today().strftime("%d/%m/%Y")
    rows = ["Date,Time,Open,High,Low,Close,Volume"]
    minute = 9 * 60 + 45           # 09:45 → 14:45 là 301 nến
    for i in range(301):
        h, m = divmod(minute + i, 60)
        px = 1960.0 + (i % 7) * 0.1
        rows.append(f"{today},{h:02d}:{m:02d},{px:.1f},{px + 0.5:.1f},"
                    f"{px - 0.5:.1f},{px:.1f},{1000 + i}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def _run_render(tmp_path, monkeypatch, in_session):
    """Render 1 lần tab Phái Sinh, trả (danh sách tin Telegram, kết quả AppTest)."""
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = streamlit_testing.AppTest

    from vn_invest import auto_trader as at
    from vn_invest import autotrade_runtime as rt
    from vn_invest import phaisinh_tab as ps
    from vn_invest import vps_telegram as tg

    cfg = {**at._DEFAULT_CFG, "enabled": True, "dry_run": True,
           "telegram_vps_reports": False, "normal_stop_enabled": False}
    monkeypatch.setattr(at, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(rt, "STATE_PATH", tmp_path / "autotrade_live_state.json")
    monkeypatch.setattr(rt, "ensure_worker", lambda: None)
    monkeypatch.setattr(tg, "ensure_worker", lambda: None)

    # Không đường nào được chạm Chrome/VPS thật trong test.
    monkeypatch.setattr(at, "check_session",
                        lambda: (False, "Không nối được http://127.0.0.1:9222: TimeoutError"))
    monkeypatch.setattr(at, "submit_signal_async", lambda *a, **k: None)
    monkeypatch.setattr(at, "close_position_async", lambda *a, **k: None)

    monkeypatch.setattr(ps, "_PS_STATE_FILE", str(tmp_path / "ps_state.json"))
    monkeypatch.setattr(ps, "_JOURNAL_FILE", str(tmp_path / "journal.csv"))
    monkeypatch.setattr(ps, "_DATA_FILE_1M", _write_feed(tmp_path / "vn30f1m_1min.csv"))
    monkeypatch.setattr(ps, "_load_ui_pref", lambda: {})   # tránh auto-gửi email báo cáo
    monkeypatch.setattr(ps, "_load_ai_system", lambda: (None, None))
    monkeypatch.setattr(ps, "_in_trading_session",
                        lambda now=None: (in_session, "test"))

    sent = []
    monkeypatch.setattr(ps, "_send_telegram_async", lambda msg: sent.append(msg))

    old_cwd = os.getcwd()
    os.chdir(APP_DIR)
    try:
        src = (
            f"import sys; sys.path.insert(0, {APP_DIR!r})\n"
            "from vn_invest.phaisinh_tab import render_phaisinh_tab\n"
            "render_phaisinh_tab()\n"
        )
        run = AppTest.from_string(src, default_timeout=180).run()
    finally:
        os.chdir(old_cwd)
    return sent, run


def _session_alerts(messages):
    return [m for m in messages if "phiên VPS có vấn đề" in m]


@pytest.mark.slow
def test_khong_canh_bao_phien_vps_ngoai_gio(tmp_path, monkeypatch):
    sent, run = _run_render(tmp_path, monkeypatch, in_session=False)
    assert not run.exception, "\n".join(str(e.value)[:400] for e in run.exception)
    assert not _session_alerts(sent), (
        "Ngoài giờ giao dịch vẫn bắn cảnh báo phiên VPS:\n" + "\n".join(sent))


@pytest.mark.slow
def test_van_canh_bao_phien_vps_trong_gio(tmp_path, monkeypatch):
    """Chốt mặt còn lại: bịt theo giờ không được làm câm cảnh báo lúc cần."""
    sent, run = _run_render(tmp_path, monkeypatch, in_session=True)
    assert not run.exception, "\n".join(str(e.value)[:400] for e in run.exception)
    assert len(_session_alerts(sent)) == 1, (
        f"Trong giờ phải cảnh báo đúng 1 lần, thực tế {len(_session_alerts(sent))}:\n"
        + "\n".join(sent))
