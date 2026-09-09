"""Entry guards use broker prices/PnL; never contact VPS."""
import pytest
from vn_invest import auto_trader as at, autotrade_runtime as rt
from .test_autotrade_runtime import live


@pytest.mark.parametrize('side,last,sl,tp', [
    ('LONG',1975.,1975.,None), ('LONG',1974.,1975.,None),
    ('SHORT',1988.,1988.,None), ('LONG',1990.,1975.,1990.),
    ('SHORT',1970.,1988.,1970.),
])
def test_crossed_protection_rejected_before_send(live,side,last,sl,tp):
    live.broker.data['positions'][0]['last']=last
    ok,msg=at.submit_signal(side,True,1981.5,True,sl,tp,'crossed',live.clock[0].isoformat())
    assert not ok and 'SL/TP' in msg
    assert not live.broker.sent
    assert not rt.load_state()['cycles']


@pytest.mark.parametrize('pnl,allowed',[(-400_000,False),(-350_000,True),(0,True)])
def test_stop_loss_fits_remaining_daily_budget(live,pnl,allowed):
    live.broker.data['pnl_vnd']=pnl
    ok,msg=live.enter()
    assert ok is allowed,msg
    assert len(live.broker.sent)==int(allowed)
    if not allowed:
        assert 'ngân sách' in msg
        assert rt.status()['attempts']==0
        assert not rt.status()['loss_latched']


def test_budget_counts_all_contracts(live):
    live.cfg['max_qty']=2
    ok,msg=at.submit_signal('LONG',False,1981.5,True,1975.,None,'two',live.clock[0].isoformat())
    assert not ok and 'ngân sách' in msg
    assert not live.broker.sent


def test_earlier_profit_does_not_expand_single_entry_budget(live):
    live.broker.data['pnl_vnd']=2_000_000
    ok,msg=at.submit_signal('LONG',True,1981.5,True,1970.,None,'wide',live.clock[0].isoformat())
    assert not ok and 'ngân sách' in msg
    assert not live.broker.sent
