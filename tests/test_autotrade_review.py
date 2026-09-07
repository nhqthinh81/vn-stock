import numpy as np
import pandas as pd
import pytest
from .conftest import FakeBrowser, FakePage


def test_session_not_ready_in_condition_mode(at_module):
    at_module.page.sim_ticket_mode = 'condition'
    ok, msg = at_module.at.check_session()
    assert not ok
    assert 'Lệnh thường' in msg


def test_find_page_skips_expired_tab(at_module):
    dead = FakePage()
    dead.url = 'https://smartpro.vps.com.vn/v1/?login=true'
    browser = FakeBrowser(dead)
    browser.contexts[0].pages.append(at_module.page)
    assert at_module.at._find_vps_page(browser, 'vps.com.vn') is at_module.page


def test_find_page_rejects_ambiguous_live_tabs(at_module):
    browser = FakeBrowser(at_module.page)
    browser.contexts[0].pages.append(FakePage())
    with pytest.raises(ValueError, match='SmartPro'):
        at_module.at._find_vps_page(browser, 'vps.com.vn')


def test_close_does_not_exceed_real_position(at_module):
    at, page = at_module.at, at_module.page
    cfg = at.load_config()
    cfg.update(enabled=True, dry_run=False)
    at.save_config(cfg)
    page.sim_position = ('SHORT', 1)
    ok, _ = at.close_position('SHORT', qty=2, price=1981.5)
    assert not ok
    assert not page.clicked and not page.evaluated_close


@pytest.mark.parametrize('side,qty', [('INVALID', 1), ('LONG', 0), ('LONG', -1), ('LONG', 1.5)])
def test_close_rejects_invalid_input(at_module, side, qty):
    at, page = at_module.at, at_module.page
    cfg = at.load_config()
    cfg.update(enabled=True, dry_run=False)
    at.save_config(cfg)
    page.sim_position = ('LONG', 2)
    ok, _ = at.close_position(side, qty=qty)
    assert not ok and not page.evaluated_close


@pytest.mark.parametrize('price', [float('nan'), float('inf'), -1, 0])
def test_invalid_entry_price_never_reaches_ticket(at_module, price):
    at, page = at_module.at, at_module.page
    cfg = at.load_config()
    cfg.update(enabled=True, dry_run=False)
    at.save_config(cfg)
    ok, _ = at.submit_signal('LONG', True, price=price)
    assert not ok and not page.selected and not page.clicked


@pytest.mark.parametrize('close,hist_prev,hist_last,expected', [
    (100, 1, 1, 'WAIT'), (99, 1, 1, 'WAIT'),
    (100, 2, 1, 'WAIT'), (101, 1, 2, 'LONG'), (99, 2, 1, 'SHORT'),
    (101, 1, float('nan'), 'WAIT'), (101, 1, float('inf'), 'WAIT'),
])
def test_rule_uses_histogram_and_strict_comparisons(ps_module, monkeypatch, close, hist_prev, hist_last, expected):
    import pandas_ta as ta
    ps = ps_module
    ix = pd.date_range('2026-09-07 09:00', periods=65, freq='min')
    df = pd.DataFrame({'Close': close, 'Open': close, 'High': close+1,
                       'Low': close-1, 'Volume': 100}, index=ix)
    hist = pd.Series(hist_prev, index=ix, dtype=float)
    hist.iloc[-1] = hist_last
    # Deliberately make signal move in the opposite direction to histogram.
    signal = -hist if hist_last != hist_prev else pd.Series(np.linspace(3, 1, len(ix)), index=ix)
    macd = pd.DataFrame({'MACD_12_26_9': 0, 'MACDh_12_26_9': hist, 'MACDs_12_26_9': signal})
    monkeypatch.setattr(ps, '_closed_bars', lambda *args: df.copy())
    monkeypatch.setattr(ps, '_calc_vwap', lambda bars: pd.Series(100., index=bars.index))
    monkeypatch.setattr(ps, '_get_atr_state', lambda bars: (1., 1.))
    monkeypatch.setattr(ta, 'macd', lambda *args, **kwargs: macd)
    actual, reason, detail = ps._get_rule_signal(df)
    assert actual == expected, reason
    if np.isfinite(hist_last):
        assert detail['mh'] == hist_last
    else:
        assert not detail


@pytest.mark.parametrize('matches,status,expected_ok', [
    (True, 'Đã khớp', True), (True, 'Chờ khớp', True),
    (False, 'Đã khớp', False), (True, 'Từ chối', False),
    (True, 'Trạng thái chưa biết', False),
])
def test_submit_reconciles_matching_order(at_module, monkeypatch, matches, status, expected_ok):
    at, page = at_module.at, at_module.page
    cfg = at.load_config()
    snapshots = iter([[], [{'id': 'NEW', 'matches': matches, 'status': status}]])
    monkeypatch.setattr(at, '_order_snapshot', lambda *args: next(snapshots, []))
    ticks = iter(range(0, 300, 10))
    monkeypatch.setattr(at.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(at.time, 'sleep', lambda *args: None)
    ok, msg = at._submit(page, cfg, 'LONG')
    assert ok is expected_ok, msg
    assert page.clicked.count('#btn_long') == 1


def test_position_change_alone_does_not_confirm_order(at_module, monkeypatch):
    at, page = at_module.at, at_module.page
    monkeypatch.setattr(at, '_order_snapshot', lambda *args: [])
    ticks = iter(range(0, 300, 10))
    monkeypatch.setattr(at.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(at.time, 'sleep', lambda *args: None)
    ok, msg = at._submit(page, at.load_config(), 'LONG')
    assert page.sim_position == ('LONG', 1)
    assert not ok and 'CHƯA RÕ' in msg
    assert page.clicked.count('#btn_long') == 1


@pytest.mark.parametrize('raw', [None, 'bad', 'nan', 'inf', '1.5'])
def test_unreadable_position_is_unknown(at_module, monkeypatch, raw):
    monkeypatch.setattr(at_module.page, 'evaluate', lambda *args: raw)
    assert at_module.at._read_position(at_module.page, '41I1G9000') == ('UNKNOWN', 0)


@pytest.mark.parametrize('max_qty', [0, -1, 1.5, True, 'bad'])
def test_invalid_quantity_config_never_submits(at_module, max_qty):
    at, page = at_module.at, at_module.page
    cfg = at.load_config()
    cfg.update(enabled=True, dry_run=False, max_qty=max_qty)
    at.save_config(cfg)
    ok, _ = at.submit_signal('LONG', True, price=1981.5)
    assert not ok and not page.clicked


def test_submit_waits_for_delayed_confirmation(at_module, monkeypatch):
    at, page = at_module.at, at_module.page
    cfg = at.load_config()
    calls = []
    def delayed_modal(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise TimeoutError('modal not yet visible')
    monkeypatch.setattr(page, 'wait_for_selector', delayed_modal)
    ok, _ = at._submit(page, cfg, 'LONG')
    assert ok and len(calls) == 2
    assert page.clicked.count('#acceptCreateOrderNew') == 1
    assert page.clicked.count('#btn_long') == 1


def test_order_can_arrive_after_old_six_second_window(at_module, monkeypatch):
    at, page = at_module.at, at_module.page
    snapshots = iter([[], [], [{'id': 'LATE', 'matches': True, 'status': 'Đã khớp'}]])
    monkeypatch.setattr(at, '_order_snapshot', lambda *args: next(snapshots, []))
    ticks = iter([0, 1, 13])
    monkeypatch.setattr(at.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(at.time, 'sleep', lambda *args: None)
    ok, _ = at._submit(page, at.load_config(), 'LONG')
    assert ok
    assert page.clicked.count('#btn_long') == 1
