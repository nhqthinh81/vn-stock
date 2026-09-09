"""Protective Stop lifecycle: all broker mutations are in-memory."""
from copy import deepcopy
from datetime import timedelta
import pytest
from vn_invest import autotrade_runtime as rt, vps_stop_guard as sg, vps_telegram as tg
from .test_autotrade_runtime import live


@pytest.fixture
def protect(live,monkeypatch):
    b=live.broker
    b.data['positions'][0].update(net=1,avg=1981.5)
    b.data['orders']=[dict(id='manual',number='manual',symbol='41I1G9000',side='B',qty=1,
                           filled=1,avg=1981.5,price=1981.5,state='FILLED')]
    monkeypatch.setattr(sg,'_ATR',dict(symbol='41I1G9000',at=live.clock[0],atr=1.,close=1981.5))
    original=b.materialize
    def materialize(kind,intent):
        if kind=='protect_stop':
            key='stop-'+str(len(b.data['conditions']))
            b.data['conditions'].append(dict(id=key,number=key,symbol=intent['symbol'],type='stop',
                subtype=None,side='S' if intent['side']=='SHORT' else 'B',qty=intent['qty'],remaining=0,  # VPS: REMAIN_QTY=0 khi con PENDING_TRIGGER
                trigger=intent['trigger'],relation=intent['relation'],price_type='MTL',status='PENDING_TRIGGER',
                order_status='Pending_New',parent='null',parent_number='null',child='null',child_number='null'))
        else:original(kind,intent)
    monkeypatch.setattr(b,'materialize',materialize)
    return live


def current():return rt.load_state()['normal_stops'][-1]


def trigger(protect,filled=None,state='FILLED'):
    row=protect.broker.data['conditions'][0]
    qty=row['qty'];filled=qty if filled is None else filled
    row.update(status='TRIGGERED',order_status='Filled' if state=='FILLED' else 'Partial_filled',child='child',child_number='child')
    sign=-1 if row['side']=='S' else 1
    protect.broker.data['orders'].append(dict(id='child',number='child',symbol=row['symbol'],side=row['side'],
        qty=qty,filled=filled,avg=row['trigger'],price='MTL',state=state))
    protect.broker.data['positions'][0]['net']+=sign*filled


@pytest.mark.parametrize('net,side,relation,threshold',[(1,'SHORT','LTEQ',1978.5),(-1,'LONG','GTEQ',1984.5)])
def test_normal_position_gets_opposite_stop(protect,net,side,relation,threshold):
    protect.broker.data['positions'][0]['net']=net
    protect.broker.data['orders'][0]['side']='B' if net>0 else 'S'
    assert rt.tick()[0];assert rt.tick()[0]
    assert [k for k,_ in protect.broker.sent]==['protect_stop']
    intent=protect.broker.sent[0][1]
    assert (intent['side'],intent['relation'],intent['trigger'],intent['qty'])==(side,relation,threshold,1)
    assert current()['state']=='PROTECTED'


def test_pending_unfilled_order_not_protected(protect):
    protect.broker.data['positions'][0]['net']=0
    protect.broker.data['orders'][0].update(filled=0,state='PENDING')
    rt.tick();assert not protect.broker.sent


def test_partial_entry_protects_only_confirmed_fill(protect):
    protect.broker.data['orders'][0].update(qty=3,state='PARTIAL')
    rt.tick();assert protect.broker.sent[0][1]['qty']==1


def test_unknown_stop_blocks_resend_and_new_bot_cycle(protect):
    protect.broker.outcome='UNKNOWN'
    rt.tick();rt.tick();rt.tick()
    assert len(protect.broker.sent)==1 and current()['state']=='UNKNOWN'
    protect.broker.data['positions'][0]['net']=0
    assert not protect.enter()[0]
    assert len(protect.broker.sent)==1


def test_delayed_stop_ack_reconciles(protect):
    protect.broker.delayed=True
    rt.tick()
    protect.broker.materialize(*protect.broker.sent[0])
    rt.tick()
    assert current()['state']=='PROTECTED' and len(protect.broker.sent)==1


@pytest.mark.parametrize('typ',['sl_tp','stop','oco','trailing_stop'])
def test_existing_condition_never_gets_duplicate(protect,typ):
    protect.broker.data['conditions']=[dict(id='foreign',symbol='41I1G9000',type=typ,status='PENDING_TRIGGER',order_status='Pending_New')]
    rt.tick();assert not protect.broker.sent


def test_stale_atr_no_new_stop(protect,monkeypatch):
    monkeypatch.setattr(sg,'_ATR',dict(sg._ATR,at=protect.clock[0]-timedelta(minutes=3)))
    rt.tick();assert not protect.broker.sent
    assert 'ATR' in rt.status()['stop_guard_message']


def test_readonly_tick_never_mutates(protect):
    rt.tick(allow_actions=False)
    assert not protect.broker.sent


def test_loss_cap_does_not_block_protection(protect):
    protect.broker.data['pnl_vnd']=-1000000
    rt.tick()
    assert protect.broker.sent[0][0]=='protect_stop'
    assert rt.status()['loss_latched']


def test_stale_position_not_equal_fills_blocks_stop(protect):
    protect.broker.data['positions'][0]['net']=2
    rt.tick();assert not protect.broker.sent


def test_fill_closes_without_second_exit(protect):
    rt.tick();rt.tick();trigger(protect)
    rt.tick();rt.tick()
    assert current()['state']=='CLOSED'
    assert [k for k,_ in protect.broker.sent]==['protect_stop']


def test_filled_stop_stale_position_does_not_rearm(protect):
    rt.tick();rt.tick();trigger(protect)
    protect.broker.data['positions'][0]['net']=1
    rt.tick();rt.tick()
    assert current()['state']=='UNKNOWN' and len(protect.broker.sent)==1


def test_manual_flat_cancels_owned_stop(protect):
    rt.tick();rt.tick()
    row=deepcopy(protect.broker.data['orders'][0]);row.update(id='manual-close',number='manual-close',side='S')
    protect.broker.data['orders'].append(row)
    protect.broker.data['positions'][0]['net']=0
    rt.tick();rt.tick()
    assert [k for k,_ in protect.broker.sent]==['protect_stop','cancel_condition']
    assert current()['state']=='CLOSED'


def test_resize_waits_cancel_before_replacing(protect):
    rt.tick();rt.tick()
    protect.broker.data['orders'][0].update(qty=2,filled=2)
    protect.broker.data['positions'][0]['net']=2
    rt.tick();assert protect.broker.sent[-1][0]=='cancel_condition'
    rt.tick();assert len(protect.broker.sent)==2
    rt.tick();assert protect.broker.sent[-1][0]=='protect_stop'
    assert protect.broker.sent[-1][1]['qty']==2


def test_cancel_timeout_keeps_gate_closed(protect):
    rt.tick();rt.tick()
    protect.broker.outcome='UNKNOWN'
    protect.broker.data['positions'][0]['net']=0
    rt.tick();rt.tick();rt.tick()
    assert [k for k,_ in protect.broker.sent]==['protect_stop','cancel_condition']


def test_trigger_during_cancel_reconciles_instead_of_replacing(protect):
    rt.tick();rt.tick()
    protect.broker.delayed=True
    protect.broker.data['positions'][0]['net']=0
    rt.tick()
    protect.broker.data['positions'][0]['net']=1
    trigger(protect)
    rt.tick();rt.tick()
    assert current()['state']=='CLOSED'
    assert [k for k,_ in protect.broker.sent]==['protect_stop','cancel_condition']


def test_partial_child_stays_managed(protect):
    protect.broker.data['orders'][0].update(qty=2,filled=2)
    protect.broker.data['positions'][0]['net']=2
    rt.tick();rt.tick();trigger(protect,1,'PARTIAL')
    rt.tick();rt.tick()
    assert current()['state']=='TRIGGERED' and current()['child_filled']==1
    assert len(protect.broker.sent)==1


def test_pending_manual_close_cancels_stop(protect):
    rt.tick();rt.tick()
    row=deepcopy(protect.broker.data['orders'][0]);row.update(id='close',number='close',side='S',filled=0,state='PENDING')
    protect.broker.data['orders'].append(row)
    rt.tick();rt.tick();rt.tick()
    assert [k for k,_ in protect.broker.sent]==['protect_stop','cancel_condition']


def test_missing_known_stop_never_recreates(protect):
    rt.tick();rt.tick();protect.broker.data['conditions']=[]
    rt.tick();assert current()['state']=='UNKNOWN' and len(protect.broker.sent)==1


def test_rejected_stop_is_not_retried(protect):
    protect.broker.outcome='REJECTED'
    rt.tick();rt.tick();rt.tick()
    assert current()['state']=='REJECTED' and len(protect.broker.sent)==1


def test_telegram_labels_generated_child_automatic(protect):
    rt.tick();rt.tick();trigger(protect);rt.tick()
    assert 'child' in tg.automatic_ids(rt.load_state(),protect.broker.snapshot())


def test_disabled_option_keeps_existing_stop_tracking(protect):
    rt.tick();rt.tick();protect.cfg['normal_stop_enabled']=False
    rt.tick();assert current()['state']=='PROTECTED'
    assert len(protect.broker.sent)==1


def test_second_snapshot_change_blocks_dispatch(protect,monkeypatch):
    original=protect.broker.snapshot
    count=[0]
    def snapshot(since=None):
        count[0]+=1
        out=original(since)
        if count[0]==2:out['positions'][0]['net']=0
        return out
    monkeypatch.setattr(protect.broker,'snapshot',snapshot)
    rt.tick();assert not protect.broker.sent



def test_existing_stop_survives_missing_atr_after_restart(protect,monkeypatch):
    rt.tick();rt.tick()
    monkeypatch.setattr(sg,'_ATR',None)
    rt.tick()
    assert current()['state']=='PROTECTED' and len(protect.broker.sent)==1


def test_guard_off_does_not_create(protect):
    protect.cfg['normal_stop_enabled']=False
    rt.tick();assert not protect.broker.sent


def test_atc_no_new_stop(protect):
    protect.clock[0]=protect.clock[0].replace(hour=14,minute=31)
    rt.tick();assert not protect.broker.sent


def test_known_child_fill_regression_is_blocked(protect):
    protect.broker.data['orders'][0].update(qty=2,filled=2)
    protect.broker.data['positions'][0]['net']=2
    rt.tick();rt.tick();trigger(protect,1,'PARTIAL');rt.tick()
    protect.broker.data['orders'][-1]['filled']=0
    protect.broker.data['positions'][0]['net']=2
    rt.tick()
    assert current()['state']=='UNKNOWN' and current()['child_filled']==1
    assert len(protect.broker.sent)==1


def test_ambiguous_stop_matches_hold_gate(protect):
    rt.tick()
    row=deepcopy(protect.broker.data['conditions'][0]);row.update(id='duplicate',number='duplicate')
    protect.broker.data['conditions'].append(row)
    rt.tick()
    assert current()['state']=='UNKNOWN' and len(protect.broker.sent)==1


def test_foreign_condition_added_cancels_own_only(protect):
    rt.tick();rt.tick()
    row=deepcopy(protect.broker.data['conditions'][0]);row.update(id='manual-stop',number='manual-stop')
    protect.broker.data['conditions'].append(row)
    rt.tick();rt.tick();rt.tick()
    assert protect.broker.sent[-1][0]=='cancel_condition'
    assert protect.broker.sent[-1][1]['target']!='manual-stop'
    assert len(protect.broker.sent)==2


def test_closed_bars_atr_publication_rejects_bad_data(protect,monkeypatch):
    import pandas as pd
    import numpy as np
    monkeypatch.setattr(sg,'_ATR',None)
    frame=pd.DataFrame({'High':[1982.]*30,'Low':[1980.]*30,'Close':[1981.]*30},
        index=pd.date_range(end=protect.clock[0],periods=30,freq='min'))
    sg.observe_closed_bars(frame,'41I1G9000')
    assert sg.stop_distance('41I1G9000',1981.,protect.clock[0],protect.cfg)>0
    monkeypatch.setattr(sg,'_ATR',None)
    frame.iloc[-1,0]=np.nan
    sg.observe_closed_bars(frame,'41I1G9000')
    with pytest.raises(ValueError):sg.stop_distance('41I1G9000',1981.,protect.clock[0],protect.cfg)



@pytest.mark.parametrize('change',[{'remaining':'NaN'},{'remaining':-1},{'remaining':2}])
def test_owned_pending_stop_without_verified_quantity_is_not_protected(protect,change):
    assert rt.tick()[0]
    protect.broker.data['conditions'][0].update(change)
    count=len(protect.broker.sent)
    assert rt.tick(allow_actions=False)[0]
    assert current()['state']=='UNKNOWN'
    assert 'chưa xác nhận Stop bảo vệ đủ' in rt.status()['stop_guard_message']
    assert len(protect.broker.sent)==count


@pytest.mark.parametrize('net,side,relation,trigger',[(1,'S','LTEQ',1978.5),(-1,'B','GTEQ',1984.5)])
def test_stop_coverage_requires_correct_trigger_relation(protect,net,side,relation,trigger):
    snapshot=protect.broker.snapshot();pos=snapshot['positions'][0];pos['net']=net
    row=dict(id='stop',symbol=pos['symbol'],type='stop',status='PENDING_TRIGGER',
             side=side,relation=relation,trigger=trigger,qty=1,remaining=1,price_type='MTL')
    snapshot['conditions']=[row]
    assert sg.protection_coverage(snapshot,pos)['confirmed']
    row['relation']='GTEQ' if relation=='LTEQ' else 'LTEQ'
    assert not sg.protection_coverage(snapshot,pos)['confirmed']



def test_previous_day_child_id_is_never_matched_to_new_day_order(protect):
    rt.tick();rt.tick()
    trigger(protect,filled=0,state='PENDING')
    rt.tick(allow_actions=False)
    count=len(protect.broker.sent)
    protect.clock[0]+=timedelta(days=1)
    rt.tick()
    assert current()['state']=='UNKNOWN'
    assert 'không ghép ID sang phiên mới' in rt.status()['stop_guard_message']
    assert len(protect.broker.sent)==count


# Hàng dữ liệu THẬT đọc từ VPS lúc 13:52 ngày 09/09/2026 (đã bỏ id/số hiệu thật).
# REMAIN_QTY = 0 dù lệnh điều kiện đang PENDING_TRIGGER và bảo vệ đủ 1 HĐ —
# đọc 0 thành "chưa có gì bảo vệ" khiến runtime tự hủy SL/TP của chính nó rồi
# thoát vị thế; VPS từ chối lệnh thoát và bot đóng băng, để vị thế thật nằm trần.
def _live_row(**over):
    row = dict(id='STOP_LIVE', number='n1', symbol='41I1G9000', type='sl_tp',
               subtype='SL', side='B', qty=1, remaining=0, trigger=1973.4,
               relation='GTEQ', status='PENDING_TRIGGER', order_status='Pending_New',
               parent='null', parent_number='null', child='null', child_number='null')
    row.update(over)
    return row


def test_pending_condition_with_zero_remain_qty_counts_as_protection(protect):
    """REMAIN_QTY=0 khi chờ kích hoạt nghĩa là CẢ qty đang bảo vệ, không phải 0."""
    snapshot = protect.broker.snapshot()
    pos = snapshot['positions'][0]
    pos.update(net=-1, avg=1971.2, last=1971.3)
    snapshot['conditions'] = [_live_row()]
    cover = sg.protection_coverage(snapshot, pos)
    assert cover['confirmed'], cover
    assert cover['sl_qty'] == 1 and cover['required_qty'] == 1


def test_zero_remain_qty_also_counts_for_standalone_stop(protect):
    snapshot = protect.broker.snapshot()
    pos = snapshot['positions'][0]
    pos.update(net=-1, avg=1971.2, last=1971.3)
    snapshot['conditions'] = [_live_row(type='stop', subtype=None, price_type='MTL')]
    assert sg.protection_coverage(snapshot, pos)['confirmed']


@pytest.mark.parametrize('bad', [
    {'remaining': -1},          # âm: dữ liệu hỏng
    {'remaining': 2},           # nhiều hơn qty: không nhất quán
    {'remaining': 'NaN'},
    {'remaining': 0.5},         # không nguyên
])
def test_inconsistent_remain_qty_is_still_rejected(protect, bad):
    snapshot = protect.broker.snapshot()
    pos = snapshot['positions'][0]
    pos.update(net=-1, avg=1971.2, last=1971.3)
    snapshot['conditions'] = [_live_row(**bad)]
    assert not sg.protection_coverage(snapshot, pos)['confirmed']


def test_partial_remain_qty_is_trusted_when_broker_reports_it(protect):
    """Nếu broker CÓ điền remaining thì tôn trọng nó (khớp một phần)."""
    snapshot = protect.broker.snapshot()
    pos = snapshot['positions'][0]
    pos.update(net=-2, avg=1971.2, last=1971.3)
    snapshot['conditions'] = [_live_row(qty=2, remaining=1)]
    cover = sg.protection_coverage(snapshot, pos)
    assert not cover['confirmed'] and cover['sl_qty'] == 1 and cover['required_qty'] == 2
