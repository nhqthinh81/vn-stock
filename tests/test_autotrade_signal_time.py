"""Closed one-minute signal freshness; fake broker only, no live trading."""
from datetime import timedelta
import pytest
from vn_invest import auto_trader as at, autotrade_runtime as rt
from vn_invest import phaisinh_tab as ps
from .test_autotrade_runtime import live


def submit_bar(live,bar_time,key='bar-1'):
    return at.submit_signal('LONG',True,1981.5,True,1975,None,key,
                            ps._signal_available_at(bar_time).isoformat())


@pytest.mark.parametrize('minute',[0,32,40])
def test_morning_signal_127_seconds_from_bar_start_is_fresh(live,minute):
    bar=live.clock[0].replace(hour=9,minute=minute,second=0)
    live.clock[0]=bar+timedelta(seconds=127)
    ok,msg=submit_bar(live,bar)
    assert ok,msg
    assert len(live.broker.sent)==1


@pytest.mark.parametrize('seconds,expected',[(179,True),(180,True),(181,False),(600,False)])
def test_age_limit_still_applies_from_close(live,seconds,expected):
    bar=live.clock[0]
    live.clock[0]=bar+timedelta(seconds=seconds)
    assert submit_bar(live,bar)[0] is expected
    assert len(live.broker.sent)==int(expected)


def test_signal_expires_while_reading_broker(live,monkeypatch):
    bar=live.clock[0]
    live.clock[0]=bar+timedelta(seconds=170)
    original=live.broker.snapshot
    def slow_snapshot(since=None):
        live.clock[0]+=timedelta(seconds=11)
        return original(since)
    monkeypatch.setattr(live.broker,'snapshot',slow_snapshot)
    ok,msg=submit_bar(live,bar)
    assert not ok and 'hết hiệu lực' in msg
    assert not live.broker.sent


@pytest.mark.parametrize('offset',[timedelta(days=-1),timedelta(minutes=1)])
def test_old_session_or_future_bar_not_sent(live,offset):
    assert not submit_bar(live,live.clock[0]+offset)[0]
    assert not live.broker.sent


def test_restart_does_not_refresh_signal_timestamp(live):
    bar=live.clock[0]-timedelta(seconds=127)
    assert submit_bar(live,bar)[0]
    assert not submit_bar(live,bar)[0]
    assert len(live.broker.sent)==1


def test_ui_passes_close_time_but_keeps_bar_based_identity():
    import inspect
    source=inspect.getsource(ps._live_panel_body)
    assert 'signal_at=_signal_available_at(closed_ts).isoformat()' in source
    assert 'f"{closed_ts.isoformat()}:{ai_signal}"' in source
