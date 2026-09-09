"""Render thật tab Phái Sinh bằng AppTest — bắt NameError mà py_compile bỏ qua.

Chuyển từ scratchpad/test_render.py sang pytest chính thức. Chậm (~vài chục
giây, khởi động cả Streamlit runtime giả lập) nên tách riêng, đánh dấu `slow`
để có thể loại trừ bằng `pytest -m "not slow"` khi chỉ cần chạy nhanh.

⚠️ Render tab Phái Sinh gọi `_live_panel_body()`, hàm này `ensure_worker()` khi
`data/autotrade_config.json` thật đang `enabled=true, dry_run=false`. Worker là
daemon thread: nó SỐNG QUA test này, lặp `tick()` mỗi 5 giây, nối Chrome/VPS
thật và ghi đè `data/autotrade_live_state.json` thật — đua ghi với mọi test sau
đó (đã thấy file journal bị nối 2 bản → JSONDecodeError trong test qua đêm). Vì
vậy test này BẮT BUỘC ép cấu hình an toàn + trỏ STATE_PATH sang tmp trước khi
render.
"""
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.slow
def test_phaisinh_tab_renders_without_exception(tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = streamlit_testing.AppTest

    from vn_invest import auto_trader as at
    from vn_invest import autotrade_runtime as rt
    from vn_invest import vps_telegram as tg

    safe_cfg = {**at._DEFAULT_CFG, "enabled": False, "dry_run": True,
                "telegram_vps_reports": False}
    monkeypatch.setattr(at, "load_config", lambda: dict(safe_cfg))
    monkeypatch.setattr(rt, "STATE_PATH", tmp_path / "autotrade_live_state.json")
    monkeypatch.setattr(rt, "ensure_worker", lambda: None)
    monkeypatch.setattr(tg, "ensure_worker", lambda: None)

    old_cwd = os.getcwd()
    os.chdir(APP_DIR)
    try:
        src = (
            f"import sys; sys.path.insert(0, {APP_DIR!r})\n"
            "from vn_invest.phaisinh_tab import render_phaisinh_tab\n"
            "render_phaisinh_tab()\n"
        )
        at_run = AppTest.from_string(src, default_timeout=180).run()
    finally:
        os.chdir(old_cwd)

    details = "\n".join(f"  !! {str(e.value)[:400]}" for e in at_run.exception)
    assert not at_run.exception, f"render loi:\n{details}"
