"""The daily limit now uses authenticated VPS VM and a durable daily latch."""
from .test_autotrade_runtime import live
from vn_invest import auto_trader as at
from vn_invest import autotrade_runtime as rt


def test_virtual_journal_cannot_trip_or_release_real_loss_limit(live,monkeypatch):
    def virtual_must_not_be_used(*args):
        raise AssertionError('virtual PnL is not the live risk source')
    monkeypatch.setattr(at,'_today_realized_loss_vnd',virtual_must_not_be_used)
    assert live.enter()[0]
    live.broker.data['pnl_vnd']=-1_000_000
    rt.tick(allow_actions=False)
    assert rt.status()['loss_latched']
    live.broker.data['pnl_vnd']=0
    rt.tick(allow_actions=False)
    assert rt.status()['loss_latched']


def test_normal_signal_policy_still_controls_live_dispatch(live):
    live.cfg['auto_all_signals']=True
    assert at.submit_signal('LONG',False,1981.5,True,1975,None,'normal',live.clock[0].isoformat())[0]
    assert [k for k,_ in live.broker.sent]==['entry']
