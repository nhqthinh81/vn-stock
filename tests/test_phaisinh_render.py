"""Render thật tab Phái Sinh bằng AppTest — bắt NameError mà py_compile bỏ qua.

Chuyển từ scratchpad/test_render.py sang pytest chính thức. Chậm (~vài chục
giây, khởi động cả Streamlit runtime giả lập) nên tách riêng, đánh dấu `slow`
để có thể loại trừ bằng `pytest -m "not slow"` khi chỉ cần chạy nhanh.
"""
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.slow
def test_phaisinh_tab_renders_without_exception():
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = streamlit_testing.AppTest

    old_cwd = os.getcwd()
    os.chdir(APP_DIR)
    try:
        src = (
            f"import sys; sys.path.insert(0, {APP_DIR!r})\n"
            "from vn_invest.phaisinh_tab import render_phaisinh_tab\n"
            "render_phaisinh_tab()\n"
        )
        at = AppTest.from_string(src, default_timeout=180).run()
    finally:
        os.chdir(old_cwd)

    details = "\n".join(f"  !! {str(e.value)[:400]}" for e in at.exception)
    assert not at.exception, f"render loi:\n{details}"
