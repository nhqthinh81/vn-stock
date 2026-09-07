"""Public close API now acts on durable live ownership, not a virtual position."""
from .test_autotrade_runtime import live
from vn_invest import auto_trader as at
from vn_invest import autotrade_runtime as rt


def test_close_request_is_durable_and_does_not_place_opposite_immediately(live):
    assert live.enter()[0]
    assert at.close_position('LONG',signal_id='signal-1')[0]
    assert len(live.broker.sent)==1
    assert rt.active_cycle(rt.load_state())['close_reason']
    for _ in range(3):rt.tick()
    assert rt.status()['cycle_state']=='FLAT'


def test_does_not_close_external_or_wrong_cycle(live):
    assert not at.close_position('LONG')[0]
    live.enter()
    assert not at.close_position('SHORT',signal_id='signal-1')[0]
    assert not at.close_position('LONG',qty=2,signal_id='signal-1')[0]
    assert not at.close_position('LONG',signal_id='older-signal')[0]
    assert len(live.broker.sent)==1


def test_close_request_survives_disconnect(live):
    live.enter()
    live.broker.fail_read=True
    assert at.close_position('LONG',signal_id='signal-1')[0]
    assert not rt.tick()[0]
    live.broker.fail_read=False
    for _ in range(3):rt.tick()
    assert rt.status()['cycle_state']=='FLAT'
