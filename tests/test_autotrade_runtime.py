"""Live lifecycle regression tests with an in-memory broker, no network."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime,timedelta
from types import SimpleNamespace
import json
import pytest
from vn_invest import autotrade_runtime as rt
from vn_invest import auto_trader as at
from vn_invest.vps_broker import VN


class FakeBroker:
    def __init__(self,clock):
        self.clock=clock
        self.data=dict(account_ref='account-hash',pnl_vnd=0.,positions=[
            dict(symbol='41I1G9000',net=0,last=1981.5,avg=0.,due='17/09/2026')],orders=[],conditions=[])
        self.sent=[]
        self.outcome='ACK'
        self.fill_qty=None
        self.delayed=False
        self.fail_read=False
        self.seq=0

    def snapshot(self,since=None):
        if self.fail_read: raise ValueError('VPS_READ_REJECTED')
        return deepcopy({**self.data,'checked_at':self.clock[0].isoformat()})

    def materialize(self,kind,intent):
        self.seq+=1
        key=str(self.seq)
        if kind in ('entry','exit'):
            filled=intent['qty'] if self.fill_qty is None else self.fill_qty
            order=dict(id=key,number=key,symbol=intent['symbol'],side='B' if intent['side']=='LONG' else 'S',
                       qty=intent['qty'],filled=filled,avg=1981.5 if filled else 0.,price=intent.get('price','MTL'),
                       state='FILLED' if filled==intent['qty'] else 'PARTIAL' if filled else 'PENDING')
            self.data['orders'].append(order)
            self.data['positions'][0]['net']+=filled*(1 if intent['side']=='LONG' else -1)
            if kind=='entry' and filled:
                self.data['conditions'].append(dict(id='SL'+key,number='S'+key,symbol=intent['symbol'],type='sl_tp',
                    side='S' if intent['side']=='LONG' else 'B',subtype='SL',status='PENDING_TRIGGER',order_status='Pending_New',qty=filled,remaining=filled,
                    trigger=intent['sl'],parent=key,parent_number=key,child='null',child_number='null'))
            if kind=='entry' and filled and intent.get('tp') is not None:
                tp=deepcopy(self.data['conditions'][-1])
                tp.update(id='TP'+key,number='T'+key,subtype='TP',trigger=intent['tp'])
                self.data['conditions'].append(tp)
        elif kind=='cancel_condition':
            next(c for c in self.data['conditions'] if c['id']==intent['target'])['status']='CANCELED'
        elif kind=='cancel_order':
            next(o for o in self.data['orders'] if o['id']==intent['target'])['state']='CANCELED'

    def send(self,kind,intent,account_ref):
        # Assert the durable record existed BEFORE crossing the broker boundary.
        saved=rt.load_state()
        assert 'UNKNOWN' in json.dumps(saved)
        assert account_ref==self.data['account_ref']
        self.sent.append((kind,deepcopy(intent)))
        if self.outcome=='ACK' and not self.delayed:
            self.materialize(kind,intent)
        return dict(outcome=self.outcome)


@pytest.fixture
def live(tmp_path,monkeypatch):
    clock=[datetime(2026,9,7,10,0,tzinfo=VN)]
    cfg=deepcopy(at._DEFAULT_CFG)
    cfg.update(enabled=True,dry_run=False,auto_all_signals=True,max_daily_loss_vnd=1_000_000)
    broker=FakeBroker(clock)
    @contextmanager
    def connection(config): yield broker
    monkeypatch.setattr(rt,'STATE_PATH',tmp_path/'autotrade_live_state.json')
    monkeypatch.setattr(rt,'connect',connection)
    monkeypatch.setattr(rt,'now_vn',lambda:clock[0])
    monkeypatch.setattr(rt,'_LAST_ERROR','')
    monkeypatch.setattr(at,'load_config',lambda:deepcopy(cfg))
    monkeypatch.setattr(rt.time,'sleep',lambda *args:None)
    ticks=iter(range(0,100000,10))
    monkeypatch.setattr(rt.time,'monotonic',lambda:next(ticks))
    def enter(key='signal-1'):
        return at.submit_signal('LONG',True,1981.5,True,1975.,None,key,clock[0].isoformat())
    return SimpleNamespace(clock=clock,cfg=cfg,broker=broker,enter=enter)


def test_entry_single_parent_with_attached_sl(live):
    ok,msg=live.enter()
    assert ok,msg
    assert [k for k,_ in live.broker.sent]==['entry']
    cycle=rt.active_cycle(rt.load_state())
    assert cycle['entry']['state']=='FILLED' and cycle['entry']['broker_id']=='1'
    assert cycle['condition_ids']==['SL1']
    assert rt.status()['filled']==1


def test_unknown_survives_restart_and_blocks_duplicate(live):
    live.broker.outcome='UNKNOWN'
    ok,_=live.enter()
    assert not ok
    assert rt.active_cycle(rt.load_state())['entry']['state']=='UNKNOWN'
    ok,_=live.enter('signal-2')
    assert not ok and len(live.broker.sent)==1
    assert rt.tick()[0]
    assert len(live.broker.sent)==1


def test_delayed_acceptance_reconciles_without_resend(live):
    live.broker.delayed=True
    assert not live.enter()[0]
    kind,intent=live.broker.sent[0]
    live.broker.materialize(kind,intent)
    assert rt.tick()[0]
    assert rt.status()['cycle_state']=='OPEN'
    assert len(live.broker.sent)==1


def test_same_signal_dedup_after_closed(live):
    assert live.enter()[0]
    assert rt.request_close('LONG',signal_id='signal-1')[0]
    for _ in range(3): assert rt.tick()[0]
    assert rt.status()['cycle_state']=='FLAT'
    n=len(live.broker.sent)
    assert not live.enter()[0]
    assert len(live.broker.sent)==n


def test_cancel_protection_before_close_and_wait_for_fill(live):
    assert live.enter()[0]
    assert rt.request_close('LONG',signal_id='signal-1')[0]
    rt.tick()
    assert live.broker.sent[-1][0]=='cancel_condition'
    assert live.broker.data['positions'][0]['net']==1
    rt.tick()
    assert live.broker.sent[-1][0]=='exit'
    assert rt.status()['cycle_state']=='CLOSING'  # acknowledgement isn't a closed position
    rt.tick()
    assert rt.status()['cycle_state']=='FLAT'


def test_cancel_timeout_never_closes_or_resends(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    live.broker.outcome='UNKNOWN'
    rt.tick();rt.tick();rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition']
    assert rt.status()['cycle_state']=='CLOSING'


def test_partial_fill_cancel_parent_then_protection_then_close(live):
    # Synthetic budget permits this reconciliation fixture's large position.
    live.cfg['max_daily_loss_vnd']=2_000_000
    live.cfg['max_qty']=2
    live.broker.fill_qty=1
    ok,_=at.submit_signal('LONG',False,1981.5,True,1975.,None,'partial',live.clock[0].isoformat())
    assert ok
    live.clock[0]+=timedelta(seconds=61)
    rt.tick()
    assert live.broker.sent[-1][0]=='cancel_order'
    rt.tick()
    assert live.broker.sent[-1][0]=='cancel_condition'
    live.broker.fill_qty=None
    rt.tick()
    assert live.broker.sent[-1][0]=='exit' and live.broker.sent[-1][1]['qty']==1
    rt.tick()
    assert rt.status()['cycle_state']=='FLAT'


def test_loss_latched_for_whole_day_even_if_pnl_recovers(live):
    live.broker.data['pnl_vnd']=-1_000_000
    assert not live.enter()[0]
    assert rt.status()['loss_latched']
    live.broker.data['pnl_vnd']=100_000
    assert not live.enter('signal-2')[0]
    assert not live.broker.sent
    # Reload from disk, not a process-local flag.
    assert rt.load_state()['days']['2026-09-07']['loss_latched']
    live.clock[0]+=timedelta(days=1)
    assert live.enter('tomorrow')[0]


def test_loss_cap_still_allows_managed_exit(live):
    assert live.enter()[0]
    live.broker.data['pnl_vnd']=-1_200_000
    for _ in range(3):rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition','exit']
    assert rt.status()['loss_latched'] and rt.status()['cycle_state']=='FLAT'


@pytest.mark.parametrize('field,value',[('pnl_vnd',-1_000_000),('account_ref','wrong-account')])
def test_risk_and_account_change_block_open(live,field,value):
    rt.tick(allow_actions=False)
    live.broker.data[field]=value
    assert not live.enter()[0]
    assert not live.broker.sent


def test_existing_external_position_not_adopted(live):
    live.broker.data['positions'][0]['net']=1
    assert not live.enter()[0]
    assert not rt.request_close('LONG')[0]
    assert not live.broker.sent


def test_old_virtual_exit_cannot_close_new_real_cycle(live):
    live.enter()
    assert not rt.request_close('LONG',signal_id='old-signal')[0]
    assert not rt.active_cycle(rt.load_state()).get('close_reason')


def test_corrupt_state_never_resets_or_submits(live):
    rt.STATE_PATH.write_text('{broken',encoding='utf-8')
    assert not live.enter()[0]
    assert not live.broker.sent
    assert rt.STATE_PATH.read_text()=='{broken'


def test_rejected_entry_does_not_own_virtual_position(live):
    live.broker.outcome='REJECTED'
    assert not live.enter()[0]
    assert rt.status()['cycle_state']=='FLAT'
    assert not rt.request_close('LONG')[0]


def test_failed_snapshot_cannot_default_loss_to_zero(live):
    live.broker.fail_read=True
    assert not live.enter()[0]
    assert not live.broker.sent


def test_day_cap_reserves_attempt_before_unknown_send(live):
    live.cfg['max_orders_per_day']=1
    live.broker.outcome='UNKNOWN'
    live.enter()
    assert rt.status()['attempts']==1
    assert not live.enter('next')[0]
    assert len(live.broker.sent)==1


def test_hold_deadline_survives_virtual_position_disappearing(live):
    live.enter()
    live.clock[0]+=timedelta(minutes=31)
    for _ in range(3):rt.tick()
    assert rt.status()['cycle_state']=='FLAT'
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition','exit']


def test_missing_known_sl_halts_close(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    live.broker.data['conditions']=[]
    rt.tick()
    assert len(live.broker.sent)==1
    assert 'Thiếu lệnh điều kiện' in rt.status()['last_error']


def test_tick_read_only_cannot_send(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    rt.tick(allow_actions=False)
    assert len(live.broker.sent)==1


@pytest.mark.parametrize('hour,minute,allowed',[(8,45,False),(9,0,True),(11,26,False),(12,0,False),(13,0,True),(14,26,False),(14,31,False)])
def test_entry_trading_windows(live,hour,minute,allowed):
    live.clock[0]=live.clock[0].replace(hour=hour,minute=minute)
    assert live.enter()[0] is allowed


def test_stale_signal_not_sent(live):
    old=live.clock[0]-timedelta(minutes=3)
    assert not at.submit_signal('LONG',True,1981.5,True,1975,None,'old',old.isoformat())[0]
    assert not live.broker.sent


def test_schema_zero_loss_is_not_required_for_close(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    live.cfg['max_orders_per_day']=0
    for _ in range(3):rt.tick()
    assert rt.status()['cycle_state']=='FLAT'


def test_filled_exit_with_stale_position_never_closes_twice(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    rt.tick();rt.tick()
    live.broker.data['positions'][0]['net']=1
    for _ in range(3):rt.tick()
    assert [k for k,_ in live.broker.sent].count('exit')==1
    assert rt.status()['cycle_state']=='UNKNOWN'
    live.broker.data['positions'][0]['net']=0
    rt.tick()
    assert rt.status()['cycle_state']=='FLAT'


def test_missing_known_exit_blocks_resubmit(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    rt.tick();rt.tick();rt.tick(allow_actions=False)
    live.broker.data['orders']=live.broker.data['orders'][:1]
    live.broker.data['positions'][0]['net']=1
    rt.tick()
    assert [k for k,_ in live.broker.sent].count('exit')==1
    assert 'Thiếu lệnh thoát' in rt.status()['last_error']


def test_triggered_sl_fill_with_stale_position_never_sends_exit(live):
    live.enter()
    c=live.broker.data['conditions'][0]
    c.update(status='TRIGGERED',order_status='Filled',child='stop-child',child_number='stop-child')
    live.broker.data['orders'].append(dict(id='stop-child',number='stop-child',symbol='41I1G9000',
        side='S',qty=1,filled=1,avg=1975.,price=1975.,state='FILLED'))
    rt.request_close('LONG',signal_id='signal-1')
    rt.tick();rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry']
    live.broker.data['positions'][0]['net']=0
    rt.tick()
    assert rt.status()['cycle_state']=='FLAT'


def test_partial_exit_canceled_then_only_remaining_quantity(live):
    # Synthetic budget permits this reconciliation fixture's large position.
    live.cfg['max_daily_loss_vnd']=2_000_000
    live.cfg['max_qty']=2
    assert at.submit_signal('LONG',False,1981.5,True,1975.,None,'two',live.clock[0].isoformat())[0]
    rt.request_close('LONG',qty=2,signal_id='two')
    rt.tick()
    live.broker.fill_qty=1
    rt.tick()
    live.clock[0]+=timedelta(seconds=31)
    rt.tick()
    assert live.broker.sent[-1][0]=='cancel_order'
    rt.tick()
    assert live.broker.sent[-1][0]=='exit' and live.broker.sent[-1][1]['qty']==1
    rt.tick()
    assert rt.status()['cycle_state']=='FLAT'


def test_cancel_not_sent_can_resume_after_reenable(live):
    live.enter();rt.request_close('LONG',signal_id='signal-1')
    state=rt.load_state();cycle=rt.active_cycle(state)
    live.cfg['enabled']=False
    rt._cancel(state,cycle,live.broker,'cancel_condition','SL1','sl_tp')
    assert cycle['cancels']['cancel_condition:SL1']['state']=='NOT_SENT'
    live.cfg['enabled']=True
    rt.tick()
    assert [k for k,_ in live.broker.sent]==['entry','cancel_condition']


def test_rounded_sl_equal_entry_rejected(live):
    assert not at.submit_signal('LONG',True,1981.5,True,1981.49,None,'rounded',live.clock[0].isoformat())[0]
    assert not live.broker.sent


def test_entry_fill_with_stale_flat_portfolio_does_not_release_cycle(live):
    live.enter()
    live.broker.data['positions'][0]['net']=0
    rt.tick();rt.tick()
    assert rt.status()['cycle_state']=='UNKNOWN'
    assert [k for k,_ in live.broker.sent]==['entry']
    live.broker.data['positions'][0]['net']=1
    rt.tick()
    assert rt.status()['cycle_state']=='OPEN'


def test_live_session_check_reads_server(live,monkeypatch):
    from vn_invest import vps_broker
    monkeypatch.setattr(at,'check_connection',lambda:(True,'connected'))
    monkeypatch.setattr(vps_broker,'connect',rt.connect)
    assert at.check_session()[0]
    live.broker.fail_read=True
    assert not at.check_session()[0]
    assert not live.broker.sent



def test_rejected_exit_remains_visible_after_continuous_session(live):
    assert live.enter()[0]
    assert rt.request_close('LONG',signal_id='signal-1')[0]
    rt.tick()  # cancel protection
    live.broker.outcome='REJECTED'
    rt.tick()  # broker rejects the exit
    count=len(live.broker.sent)
    live.clock[0]=live.clock[0].replace(hour=14,minute=35)
    assert rt.tick()[0]
    assert 'Journal ghi REJECTED' in rt.status()['last_error']
    assert rt.status()['cycle_state']=='CLOSING'
    assert len(live.broker.sent)==count
