"""Overnight recovery uses fake broker and temporary journals only."""
from copy import deepcopy
from datetime import timedelta
import pytest
from vn_invest import autotrade_runtime as rt, vps_stop_guard as sg
from .test_autotrade_runtime import live


@pytest.fixture
def carried(live,monkeypatch):
    original=live.broker.materialize
    def materialize(kind,intent):
        if kind!='protect_stop':return original(kind,intent)
        live.broker.data['conditions'].append(dict(id='recovery-stop',number='new-stop',
            symbol=intent['symbol'],type='stop',subtype=None,
            side='S' if intent['side']=='SHORT' else 'B',qty=intent['qty'],remaining=intent['qty'],
            trigger=intent['trigger'],relation=intent['relation'],price_type='MTL',status='PENDING_TRIGGER',
            order_status='Pending_New',parent='null',parent_number='null',child='null',child_number='null'))
    monkeypatch.setattr(live.broker,'materialize',materialize)
    assert live.enter()[0]
    live.broker.data['positions'][0]['avg']=1981.5
    for c in live.broker.data['conditions']:c['status']='CANCELED'
    live.clock[0]=live.clock[0].replace(hour=15)
    assert rt.tick(allow_actions=False)[0]
    assert rt.active_cycle(rt.load_state()).get('overnight_checkpoint')
    live.clock[0]=(live.clock[0]+timedelta(days=1)).replace(hour=9,minute=0)
    live.broker.data['orders']=[]
    live.broker.sent.clear()
    return live


def test_restore_stop_at_original_sl_and_no_new_entry(carried):
    assert rt.tick()[0]
    assert [k for k,_ in carried.broker.sent]==['protect_stop']
    intent=carried.broker.sent[0][1]
    assert intent['trigger']==1975. and intent['side']=='SHORT' and intent['qty']==1
    assert rt.tick()[0]
    assert rt.load_state()['normal_stops'][0]['state']=='PROTECTED'
    assert rt.active_cycle(rt.load_state())['state']=='OVERNIGHT'
    assert rt.tick()[0]
    assert len(carried.broker.sent)==1
    assert not carried.enter('new-signal')[0]


def test_old_entry_id_is_not_matched_to_new_day_order(carried):
    cycle=rt.active_cycle(rt.load_state());entry=deepcopy(cycle['entry'])
    orders=[dict(id=entry['broker_id'],symbol=cycle['symbol'],side='S',qty=9,filled=9,avg=1,state='FILLED',number='reused')]
    assert rt._match_intent(entry,orders,carried.clock[0].date().isoformat()) is None
    assert entry==cycle['entry']


@pytest.mark.parametrize('change',['unknown_exit','legacy_reject','missing_checkpoint','missing_condition','net_mismatch','wrong_account'])
def test_unresolved_history_never_creates_protection(carried,change):
    state=rt.load_state();cycle=rt.active_cycle(state)
    if change in ('unknown_exit','legacy_reject'):
        cycle['exits'].append(dict(id='old',state='UNKNOWN' if change=='unknown_exit' else 'REJECTED',filled=0))
    elif change=='missing_checkpoint':cycle.pop('overnight_checkpoint')
    elif change=='missing_condition':carried.broker.data['conditions']=[]
    elif change=='net_mismatch':carried.broker.data['positions'][0]['net']=2
    elif change=='wrong_account':carried.broker.data['account_ref']='other'
    rt.save_state(state)
    rt.tick()
    assert not carried.broker.sent
    assert rt.active_cycle(rt.load_state())


def test_gap_through_sl_exits_instead_of_widening_stop(carried):
    carried.broker.data['positions'][0]['last']=1974.
    assert rt.tick()[0]
    assert [k for k,_ in carried.broker.sent]==['exit']
    assert carried.broker.sent[0][1]['qty']==1
    assert rt.tick()[0]
    assert rt.status()['cycle_state']=='FLAT'


def test_gap_exit_unknown_not_retried_or_reprotected(carried):
    carried.broker.data['positions'][0]['last']=1974.
    carried.broker.outcome='UNKNOWN'
    rt.tick();rt.tick();rt.tick()
    assert [k for k,_ in carried.broker.sent]==['exit']
    assert rt.active_cycle(rt.load_state())


def test_manual_flat_is_recognized_without_order_send(carried):
    carried.broker.data['orders']=[dict(id='manual-close',symbol='41I1G9000',side='S',qty=1,filled=1,state='FILLED')]
    carried.broker.data['positions'][0]['net']=0
    rt.tick()
    assert rt.status()['cycle_state']=='FLAT'
    assert not carried.broker.sent


def test_preopen_observes_but_does_not_create_stop(carried):
    carried.clock[0]=carried.clock[0].replace(hour=8,minute=50)
    rt.tick()
    assert not carried.broker.sent
    carried.clock[0]=carried.clock[0].replace(hour=9,minute=0)
    rt.tick()
    assert [k for k,_ in carried.broker.sent]==['protect_stop']


def test_readonly_does_not_submit_stop(carried):
    rt.tick(allow_actions=False)
    assert not carried.broker.sent


def test_uncertain_restore_survives_restart_without_resend(carried):
    carried.broker.outcome='UNKNOWN'
    rt.tick();rt.tick();rt.tick()
    assert [k for k,_ in carried.broker.sent]==['protect_stop']


def test_foreign_pending_condition_is_not_canceled_or_duplicated(carried):
    row=deepcopy(carried.broker.data['conditions'][0]);row.update(id='foreign',status='PENDING_TRIGGER',subtype='TP')
    carried.broker.data['conditions'].append(row)
    rt.tick()
    assert not carried.broker.sent


def test_existing_valid_sl_is_kept(carried):
    carried.broker.data['conditions'][0]['status']='PENDING_TRIGGER'
    rt.tick()
    assert not carried.broker.sent
    assert rt.active_cycle(rt.load_state())['protection']['confirmed']


def test_restore_checks_again_before_send(carried,monkeypatch):
    original=carried.broker.snapshot;calls=[]
    def changed(since=None):
        calls.append(1)
        if len(calls)>1:carried.broker.data['positions'][0]['last']=1974.
        return original(since)
    monkeypatch.setattr(carried.broker,'snapshot',changed)
    rt.tick()
    assert not carried.broker.sent


def test_second_overnight_keeps_existing_stop_without_duplicate(carried):
    rt.tick();rt.tick()
    carried.clock[0]=carried.clock[0].replace(hour=15)
    rt.tick(allow_actions=False)
    carried.clock[0]=(carried.clock[0]+timedelta(days=1)).replace(hour=9)
    count=len(carried.broker.sent)
    rt.tick()
    assert len(carried.broker.sent)==count
    assert rt.active_cycle(rt.load_state())['state']=='OVERNIGHT'



def test_stop_fill_closes_carried_cycle_without_second_exit(carried):
    rt.tick();rt.tick()
    row=carried.broker.data['conditions'][-1]
    row.update(status='TRIGGERED',order_status='Filled',child='child-today',child_number='child-today')
    carried.broker.data['orders']=[dict(id='child-today',number='child-today',symbol=row['symbol'],side='S',qty=1,filled=1,avg=1975.,state='FILLED')]
    carried.broker.data['positions'][0]['net']=0
    rt.tick();rt.tick()
    assert rt.status()['cycle_state']=='FLAT'
    assert [k for k,_ in carried.broker.sent]==['protect_stop']


def test_daily_loss_cap_cancels_recovery_before_exit(carried):
    rt.tick();rt.tick()
    carried.broker.data['pnl_vnd']=-1_000_000
    rt.tick();rt.tick();rt.tick();rt.tick()
    assert [k for k,_ in carried.broker.sent]==['protect_stop','cancel_condition','exit']
    assert rt.status()['cycle_state']=='FLAT'


def test_partial_manual_close_restores_only_remaining_position(carried):
    state=rt.load_state();cycle=rt.active_cycle(state)
    cycle['overnight_checkpoint']['net']=2
    carried.broker.data['positions'][0]['net']=1
    carried.broker.data['orders']=[dict(id='manual',symbol=cycle['symbol'],side='S',qty=1,filled=1,state='FILLED')]
    rt.save_state(state)
    rt.tick()
    assert carried.broker.sent[0][0]=='protect_stop'
    assert carried.broker.sent[0][1]['qty']==1


def test_suspended_snapshot_date_does_not_create_stop(carried,monkeypatch):
    original=carried.broker.snapshot
    def old(since=None):
        snap=original(since);snap['checked_at']=(carried.clock[0]-timedelta(days=1)).isoformat();return snap
    monkeypatch.setattr(carried.broker,'snapshot',old)
    rt.tick()
    assert not carried.broker.sent


def test_weekend_rollover_supported(carried):
    state=rt.load_state();cycle=rt.active_cycle(state)
    cycle['overnight_checkpoint']['date']='2026-09-11'
    carried.clock[0]=carried.clock[0].replace(day=14)
    rt.save_state(state)
    rt.tick()
    assert [k for k,_ in carried.broker.sent]==['protect_stop']


def test_missing_intermediate_session_blocks_recovery(carried):
    carried.clock[0]+=timedelta(days=1)
    rt.tick()
    assert not carried.broker.sent


def test_cancel_order_id_can_be_reused_next_day(carried):
    state=rt.load_state();cycle=rt.active_cycle(state)
    cycle['cancels']['cancel_order:reused']={'state':'ACK','submitted_at':'2026-09-07T10:00:00+07:00'}
    carried.broker.data['orders']=[dict(id='reused',state='PENDING')]
    rt._cancel(state,cycle,carried.broker,'cancel_order','reused')
    assert [k for k,_ in carried.broker.sent]==['cancel_order']


def test_legacy_rejection_cannot_create_end_of_day_authorization(live):
    assert live.enter()[0]
    state=rt.load_state();cycle=rt.active_cycle(state)
    cycle['exits'].append(dict(id='legacy',kind='exit',symbol=cycle['symbol'],side='SHORT',qty=1,
        state='REJECTED',filled=0,before_ids=['1'],submitted_at=live.clock[0].isoformat()))
    rt.save_state(state)
    live.clock[0]=live.clock[0].replace(hour=15)
    rt.tick(allow_actions=False)
    cycle=rt.active_cycle(rt.load_state())
    assert not cycle.get('overnight_checkpoint')
    assert 'Chưa đủ bằng chứng' in cycle['overnight_checkpoint_status']



def test_config_symbol_change_cannot_protect_wrong_contract(carried):
    carried.cfg['symbol_code']='ANOTHER'
    carried.broker.data['positions'].append(dict(symbol='ANOTHER',net=1,avg=100.,last=101.,due='17/09/2026'))
    rt.tick()
    assert carried.broker.sent[0][1]['symbol']=='41I1G9000'


def test_missing_foreign_condition_from_checkpoint_blocks_recovery(carried):
    state=rt.load_state();cycle=rt.active_cycle(state)
    cycle['overnight_checkpoint']['condition_ids'].append('unresolved-foreign')
    rt.save_state(state)
    rt.tick()
    assert not carried.broker.sent
