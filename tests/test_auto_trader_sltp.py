"""SL/TP must be attached to ONE parent, never appended via a second opening."""
from .test_autotrade_runtime import live
from vn_invest import auto_trader as at
from vn_invest import autotrade_runtime as rt


def test_legacy_post_fill_sltp_is_disabled(at_module):
    at,page=at_module.at,at_module.page
    ok,_=at._place_sltp(page,at.load_config(),'LONG',1,1981.5,1975,None)
    assert not ok and not page.evaluated_sltp


def test_parent_contains_protection_and_no_second_entry(live,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('second parent order')
    monkeypatch.setattr(at,'_place_sltp',forbidden)
    assert live.enter()[0]
    for _ in range(4):rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry']
    assert live.broker.sent[0][1]['sl']==1975


def test_filled_position_waits_for_confirmed_condition_cancel(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    live.broker.outcome='UNKNOWN'
    for _ in range(4):rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition']
    assert live.broker.data['positions'][0]['net']==1
