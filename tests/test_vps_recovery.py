"""Rejected-exit recovery, entirely in-memory broker and temporary journal."""
from copy import deepcopy
import pytest
from vn_invest import autotrade_runtime as rt
from .test_vps_stop_guard import protect
from .test_autotrade_runtime import live


@pytest.fixture
def rejected(protect):
    b=protect.broker
    b.data['positions'][0]['net']=0
    b.data['orders']=[]
    assert protect.enter()[0]
    rt.request_close('LONG',signal_id='signal-1')
    rt.tick()  # cancel original SL
    b.outcome='REJECTED'
    rt.tick()  # exit rejected, no server ID yet
    intent=rt.active_cycle(rt.load_state())['exits'][-1]
    b.data['orders'].append(dict(id='reject',number='reject',symbol=intent['symbol'],side='S',
        qty=1,filled=0,avg=0.,price='MTL',state='REJECTED'))
    b.outcome='ACK'
    return protect


def stops(live):return [i for k,i in live.broker.sent if k=='protect_stop']


def test_recovery_keeps_old_sl_once_and_survives_restart(rejected):
    assert rt.tick()[0]
    assert len(stops(rejected))==1
    assert stops(rejected)[0]['trigger']==1975.
    assert stops(rejected)[0]['qty']==1
    for _ in range(4):assert rt.tick()[0]
    assert len(stops(rejected))==1
    assert rt.load_state()['normal_stops'][-1]['state']=='PROTECTED'
    assert rt.active_cycle(rt.load_state())['recovery_stop_attempt']
    assert not rejected.enter('another')[0]


@pytest.mark.parametrize('outcome',['UNKNOWN','REJECTED'])
def test_recovery_never_retries_uncertain_or_rejected_send(rejected,outcome):
    rejected.broker.outcome=outcome
    for _ in range(4):assert rt.tick()[0]
    assert len(stops(rejected))==1


@pytest.mark.parametrize('case',['no_server_rejection','pending_order','crossed_sl','position_changed','disabled','dry_run','stop_disabled','readonly'])
def test_recovery_guards(rejected,case):
    b=rejected.broker
    if case=='no_server_rejection':b.data['orders'].pop()
    if case=='pending_order':
        row=deepcopy(b.data['orders'][0]);row.update(id='other',state='PENDING',filled=0)
        b.data['orders'].append(row)
    if case=='crossed_sl':b.data['positions'][0]['last']=1974.
    if case=='position_changed':b.data['positions'][0]['net']=2
    if case=='disabled':rejected.cfg['enabled']=False
    if case=='dry_run':rejected.cfg['dry_run']=True
    if case=='stop_disabled':rejected.cfg['normal_stop_enabled']=False
    rt.tick(allow_actions=case!='readonly')
    assert not stops(rejected)


def test_recovery_child_closes_cycle_without_second_exit(rejected):
    rt.tick();rt.tick()
    b=rejected.broker;row=b.data['conditions'][-1]
    row.update(status='TRIGGERED',order_status='Filled',child='recovery-child',child_number='recovery-child')
    b.data['orders'].append(dict(id='recovery-child',number='recovery-child',symbol=row['symbol'],
        side='S',qty=1,filled=1,avg=1975.,price='MTL',state='FILLED'))
    b.data['positions'][0]['net']=0
    for _ in range(3):assert rt.tick()[0]
    assert rt.active_cycle(rt.load_state()) is None
    assert len([k for k,i in b.sent if k=='exit'])==1


def test_fresh_snapshot_blocks_recovery_after_manual_change(rejected,monkeypatch):
    b=rejected.broker;original=b.snapshot;calls=[]
    def snapshot(*args):
        calls.append(1)
        result=original(*args)
        if len(calls)>=2:result['positions'][0]['net']=0
        return result
    monkeypatch.setattr(b,'snapshot',snapshot)
    rt.tick()
    assert not stops(rejected)



def test_recovery_missing_stop_never_releases_or_resends(rejected):
    rt.tick();rt.tick()
    rejected.broker.data['conditions'].pop()
    for _ in range(3):assert rt.tick()[0]
    assert len(stops(rejected))==1
    assert rt.active_cycle(rt.load_state()) is not None


def test_recovery_after_day_change_does_not_use_overnight_exit(rejected):
    from datetime import timedelta
    rt.tick();rt.tick()
    rejected.clock[0]+=timedelta(days=1)
    for _ in range(3):assert rt.tick()[0]
    assert len(stops(rejected))==1
    assert len([k for k,i in rejected.broker.sent if k=='exit'])==1


def test_short_recovery_uses_long_stop(rejected):
    b=rejected.broker
    state=rt.load_state();cycle=rt.active_cycle(state)
    cycle['side']='SHORT';cycle['entry']['side']='SHORT';cycle['sl']=1988.
    cycle['exits'][-1]['side']='LONG';rt.save_state(state)
    b.data['positions'][0]['net']=-1
    b.data['orders'][0]['side']='S';b.data['orders'][-1]['side']='B'
    rt.tick();rt.tick()
    assert len(stops(rejected))==1
    assert stops(rejected)[0]['side']=='LONG'
    assert stops(rejected)[0]['relation']=='GTEQ'
    assert stops(rejected)[0]['trigger']==1988.


def test_partial_exit_recovers_remaining_quantity_only(rejected):
    b=rejected.broker;state=rt.load_state();cycle=rt.active_cycle(state)
    cycle['entry'].update(qty=2,filled=2);cycle['exits'][-1]['qty']=2
    rt.save_state(state)
    b.data['orders'][0].update(qty=2,filled=2)
    b.data['orders'][-1].update(qty=2,filled=1)
    rt.tick();rt.tick()
    assert len(stops(rejected))==1
    assert stops(rejected)[0]['qty']==1


def test_recovery_partial_child_keeps_cycle_reserved(rejected):
    rt.tick();rt.tick()
    b=rejected.broker;row=b.data['conditions'][-1]
    row.update(status='TRIGGERED',order_status='Pending_New',child='pending-child',child_number='pending-child')
    b.data['orders'].append(dict(id='pending-child',number='pending-child',symbol=row['symbol'],
        side='S',qty=1,filled=0,avg=0.,price='MTL',state='PENDING'))
    for _ in range(3):assert rt.tick()[0]
    assert rt.active_cycle(rt.load_state()) is not None
    assert len(stops(rejected))==1
    assert len([k for k,i in b.sent if k=='exit'])==1



def test_next_day_manual_flat_cancels_only_owned_recovery_stop(rejected):
    from datetime import timedelta
    rt.tick();rt.tick()
    b=rejected.broker;stop_id=b.data['conditions'][-1]['id']
    rejected.clock[0]+=timedelta(days=1)
    b.data['orders']=[dict(id='today-close',number='today-close',symbol='41I1G9000',
        side='S',qty=1,filled=1,avg=1981.,price='MTL',state='FILLED')]
    b.data['positions'][0]['net']=0
    rt.tick()
    assert b.sent[-1][0]=='cancel_condition'
    assert b.sent[-1][1]['target']==stop_id
    assert len(stops(rejected))==1


def test_recovery_does_not_release_when_known_stop_disappears_after_fill(rejected):
    rt.tick();rt.tick()
    b=rejected.broker;row=b.data['conditions'][-1]
    row.update(status='TRIGGERED',order_status='Filled',child='child',child_number='child')
    b.data['orders'].append(dict(id='child',number='child',symbol=row['symbol'],side='S',
        qty=1,filled=1,avg=1975.,price='MTL',state='FILLED'))
    b.data['positions'][0]['net']=0
    rt.tick()  # guard has just observed the terminal child
    b.data['conditions'].pop()
    rt.tick()
    assert rt.active_cycle(rt.load_state()) is not None


def test_recovery_fresh_snapshot_rechecks_contract_due_date(rejected,monkeypatch):
    b=rejected.broker;original=b.snapshot;calls=[]
    def snapshot(*args):
        calls.append(1);out=original(*args)
        if len(calls)>1:out['positions'][0]['due']=''
        return out
    monkeypatch.setattr(b,'snapshot',snapshot)
    rt.tick()
    assert not stops(rejected)



@pytest.mark.parametrize('mode',['readonly','disabled','dry_run','wrong_stop','stale_snapshot'])
def test_next_day_cleanup_keeps_action_and_identity_guards(rejected,mode,monkeypatch):
    from datetime import timedelta
    rt.tick();rt.tick()
    b=rejected.broker;rejected.clock[0]+=timedelta(days=1)
    b.data['positions'][0]['net']=0
    if mode=='disabled':rejected.cfg['enabled']=False
    if mode=='dry_run':rejected.cfg['dry_run']=True
    if mode=='wrong_stop':b.data['conditions'][-1]['trigger']+=1
    if mode=='stale_snapshot':
        original=b.snapshot
        def stale(*args):
            result=original(*args)
            result['checked_at']=(rejected.clock[0]-timedelta(seconds=31)).isoformat()
            return result
        monkeypatch.setattr(b,'snapshot',stale)
    count=len(b.sent)
    rt.tick(allow_actions=mode!='readonly')
    assert len(b.sent)==count
    assert rt.active_cycle(rt.load_state()) is not None


def test_recovery_refuses_stale_snapshot_before_submission(rejected,monkeypatch):
    from datetime import timedelta
    b=rejected.broker;original=b.snapshot
    def stale(*args):
        result=original(*args)
        result['checked_at']=(rejected.clock[0]-timedelta(seconds=31)).isoformat()
        return result
    monkeypatch.setattr(b,'snapshot',stale)
    rt.tick()
    assert not stops(rejected)
