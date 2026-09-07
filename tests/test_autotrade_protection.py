"""SL/TP branch reconciliation; fake broker only."""
from copy import deepcopy
from datetime import timedelta
import pytest
from vn_invest import auto_trader as at, autotrade_runtime as rt
from .test_autotrade_runtime import live


def enter_pair(live,qty=1):
    live.cfg['max_qty']=qty
    return at.submit_signal('LONG',False,1981.5,True,1975.,1995.,'pair',live.clock[0].isoformat())


def test_both_branches_confirmed(live):
    assert enter_pair(live)[0]
    p=rt.status()['protection']
    assert p['confirmed'] and p['sl_qty']==p['tp_qty']==1
    assert len(live.broker.sent)==1


def test_pair_per_partial_fill_aggregates_each_branch(live):
    assert enter_pair(live,2)[0]
    rows=live.broker.data['conditions']
    for row in rows:row.update(qty=1,remaining=1)
    for row in deepcopy(rows):
        row['id']+='second';row['number']+='second'
        rows.append(row)
    rt.tick()
    assert rt.status()['protection']['confirmed']
    assert rt.status()['protection']['sl_qty']==2
    assert rt.status()['protection']['tp_qty']==2
    assert len(live.broker.sent)==1


def test_missing_tp_waits_then_unwinds_without_second_entry(live):
    # Missing from the first response, not a disappearing known condition.
    live.broker.delayed=True
    assert not enter_pair(live)[0]
    live.broker.materialize(*live.broker.sent[0])
    live.broker.delayed=False
    live.broker.data['conditions']=live.broker.data['conditions'][:1]
    rt.tick()
    assert not rt.status()['protection']['confirmed']
    assert len(live.broker.sent)==1
    live.clock[0]+=timedelta(seconds=31)
    for _ in range(3):rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition','exit']
    assert rt.status()['cycle_state']=='FLAT'


@pytest.mark.parametrize('change',[
    {'trigger':1974.}, {'side':'B'}, {'remaining':0}, {'remaining':2,'qty':2},
    {'remaining':'NaN'}, {'qty':True}, {'status':'PENDING_CANCEL'},
])
def test_wrong_protection_not_reported_as_confirmed(live,change):
    assert enter_pair(live)[0]
    live.broker.data['conditions'][0].update(change)
    rt.tick(allow_actions=False)
    assert not rt.status()['protection']['confirmed']
    assert len(live.broker.sent)==1


def test_parent_condition_row_is_not_a_protective_branch(live):
    enter_pair(live)
    root=deepcopy(live.broker.data['conditions'][0])
    root.update(id='root',subtype=None,status='TRIGGERED',child='1')
    live.broker.data['conditions'].append(root)
    rt.tick()
    assert rt.status()['protection']['confirmed']
    assert rt.status()['cycle_state']=='OPEN'


def test_tp_fill_and_sibling_cancel_closes_without_new_order(live):
    enter_pair(live)
    sl,tp=live.broker.data['conditions']
    sl.update(status='CANCELED',remaining=0)
    tp.update(status='TRIGGERED',order_status='Filled',child='tp-fill',child_number='tp-fill',remaining=0)
    live.broker.data['orders'].append(dict(id='tp-fill',number='tp-fill',symbol='41I1G9000',
        side='S',qty=1,filled=1,avg=1995.,price=1995.,state='FILLED'))
    live.broker.data['positions'][0]['net']=0
    rt.tick()
    assert rt.status()['cycle_state']=='FLAT'
    assert [k for k,_ in live.broker.sent]==['entry']


def test_manual_close_cancels_both_branches_before_exit(live):
    enter_pair(live)
    rt.request_close('LONG',signal_id='pair')
    for _ in range(4):rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition','cancel_condition','exit']
    assert rt.status()['cycle_state']=='FLAT'


def test_short_pair_uses_buy_protection(live):
    assert at.submit_signal('SHORT',True,1981.5,True,1995.,1975.,'short',live.clock[0].isoformat())[0]
    assert rt.status()['protection']['confirmed']
    assert all(c['side']=='B' for c in live.broker.data['conditions'])
    rt.request_close('SHORT',signal_id='short')
    for _ in range(4):rt.tick()
    assert live.broker.sent[-1][1]['side']=='LONG'
    assert rt.status()['cycle_state']=='FLAT'


def test_protective_child_fill_cannot_regress_after_restart(live):
    enter_pair(live,2)
    sl=live.broker.data['conditions'][0]
    sl.update(status='TRIGGERED',order_status='Partial_filled',child='child',child_number='child')
    child=dict(id='child',number='child',symbol='41I1G9000',side='S',qty=2,
               filled=1,avg=1975.,price=1975.,state='PARTIAL')
    live.broker.data['orders'].append(child)
    live.broker.data['positions'][0]['net']=1
    assert rt.tick(allow_actions=False)[0]
    child['filled']=0
    live.broker.data['positions'][0]['net']=2
    assert not rt.tick()[0]
    assert len(live.broker.sent)==1
    assert rt.active_cycle(rt.load_state())['protection_fills']['child']==1
