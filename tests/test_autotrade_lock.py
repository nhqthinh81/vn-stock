"""Khoá trạng thái AutoTrade khi có TIẾN TRÌNH KHÁC đang giữ khoá.

Bối cảnh (11/09/2026): hai server Streamlit (8501 + 8502) cùng chạy worker
đối soát; mỗi tick giữ khoá file vài giây. `locked_state()` dùng khoá KHÔNG
chờ (LK_NBLCK) và không thử lại, nên `submit()` của engine đụng đúng lúc
tick bên kia đang giữ khoá là ném PermissionError ngay → lệnh thật bị bỏ,
không gửi bù. Test này tạo tiến trình con giữ khoá thật (không mock) để
chứng minh `locked_state()` phải CHỜ tới khi khoá được nhả.
"""
import os
import subprocess
import sys
import time

import pytest

from vn_invest import autotrade_runtime as rt

_HOLDER = r"""
import os, sys, time
path, hold = sys.argv[1], float(sys.argv[2])
h = open(path, 'a+b')
if h.seek(0, os.SEEK_END) == 0:
    h.write(b'0'); h.flush()
h.seek(0)
if os.name == 'nt':
    import msvcrt; msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl; fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)
print('LOCKED', flush=True)
time.sleep(hold)
"""


def _hold_lock_in_other_process(lock_path, seconds):
    proc = subprocess.Popen([sys.executable, '-c', _HOLDER, str(lock_path), str(seconds)],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == 'LOCKED'
    return proc


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / 'autotrade_live_state.json'
    monkeypatch.setattr(rt, 'STATE_PATH', p)
    return p


def test_locked_state_waits_for_other_process(state_path):
    proc = _hold_lock_in_other_process(str(state_path) + '.lock', 1.5)
    try:
        t0 = time.monotonic()
        with rt.locked_state() as state:   # trước khi sửa: PermissionError ngay lập tức
            assert isinstance(state, dict)
        waited = time.monotonic() - t0
        assert waited >= 0.5, f'không hề chờ khoá ({waited:.2f}s) — vào được là do khoá không hoạt động'
    finally:
        proc.wait(timeout=10)


def test_locked_state_gives_clear_error_after_timeout(state_path, monkeypatch):
    monkeypatch.setattr(rt, '_FILE_LOCK_TIMEOUT_SEC', 0.4)
    proc = _hold_lock_in_other_process(str(state_path) + '.lock', 3)
    try:
        with pytest.raises(RuntimeError, match='tiến trình khác'):
            with rt.locked_state():
                pass
    finally:
        proc.wait(timeout=10)


def test_lock_file_records_holder_and_worker_is_detected_by_name(state_path):
    for _ in range(3):                      # nhiều lần giữ khoá → nhãn KHÔNG nối đuôi
        with rt.locked_state():
            pass
    tag = (str(state_path) + '.lock')
    raw = open(tag, 'rb').read()
    assert raw[:1] == b'0'
    holder = raw[1:].decode('utf-8')
    assert holder == f'{os.getpid()}:MainThread', holder

    import threading, time as _t
    stop = threading.Event()
    th = threading.Thread(target=stop.wait, name=rt._WORKER_THREAD_NAME, daemon=True)
    th.start()
    try:
        assert rt._worker_alive()          # module nạp lại (_WORKER=None) vẫn thấy thread cũ
    finally:
        stop.set(); th.join(timeout=2)
    assert not rt._worker_alive()
