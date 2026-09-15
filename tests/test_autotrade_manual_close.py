"""Manual (outside-bot) close of a bot-managed position; fake broker only.

Incident 15/09/2026: bot LONG 09:01, user sold MAK by hand 09:23, VPS cancelled
the SL/TP pair, net=0. The cycle went UNKNOWN forever and blocked every later
signal (09:32, 10:09 ⭐ MẠNH).
"""
from datetime import timedelta
from vn_invest import auto_trader as at, autotrade_runtime as rt
from .test_autotrade_runtime import live


def enter_pair(live):
    return at.submit_signal('LONG',True,1981.5,True,1975.,1995.,'bot-1',live.clock[0].isoformat())


def manual_sell(live,filled=1,state='FILLED'):
    live.broker.data['orders'].append(dict(id='manual',number='manual',symbol='41I1G9000',
        side='S',qty=1,filled=filled,avg=1985. if filled else 0.,price='MAK',state=state))
    live.broker.data['positions'][0]['net']-=filled


def test_manual_close_releases_cycle_and_next_signal_enters(live):
    assert enter_pair(live)[0]
    manual_sell(live)
    for c in live.broker.data['conditions']:
        c['status']='CANCELED'
    live.clock[0]+=timedelta(minutes=1)
    assert rt.tick()[0]
    assert rt.status()['cycle_state']=='FLAT'
    assert [k for k,_ in live.broker.sent]==['entry']
    closed=rt.load_state()['cycles'][-1]
    assert 'ngoài bot' in closed['close_reason']
    ok,msg=at.submit_signal('LONG',True,1981.5,True,1975.,1995.,'bot-2',live.clock[0].isoformat())
    assert ok,msg
    assert [k for k,_ in live.broker.sent]==['entry','entry']


def test_manual_close_cancels_orphan_bot_branches_without_exit(live):
    assert enter_pair(live)[0]
    manual_sell(live)          # SL/TP still PENDING_TRIGGER on the exchange
    for _ in range(4):
        live.clock[0]+=timedelta(seconds=5)
        rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition','cancel_condition']
    assert rt.status()['cycle_state']=='FLAT'


def test_flat_portfolio_without_closing_fill_stays_locked(live):
    # Portfolio lag: net=0 but no fill explains it — must not release or cancel.
    assert enter_pair(live)[0]
    live.broker.data['positions'][0]['net']=0
    rt.tick()
    assert rt.status()['cycle_state']!='FLAT'
    assert [k for k,_ in live.broker.sent]==['entry']


def test_unfilled_manual_order_does_not_release(live):
    assert enter_pair(live)[0]
    manual_sell(live,filled=0,state='PENDING')
    rt.tick()
    assert rt.status()['cycle_state']!='FLAT'
    assert [k for k,_ in live.broker.sent]==['entry']
