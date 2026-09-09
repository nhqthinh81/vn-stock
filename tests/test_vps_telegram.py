from copy import deepcopy
from datetime import timedelta
import pytest
from vn_invest import vps_telegram as tg, autotrade_runtime as rt
from .test_autotrade_runtime import live


@pytest.fixture
def report(live,monkeypatch):
    monkeypatch.setattr(tg,'connect',rt.connect)
    monkeypatch.setattr(tg,'now_vn',lambda:live.clock[0])
    monkeypatch.setattr(tg,'_LAST_ERROR','')
    monkeypatch.setattr(tg,'HEALTH_PATH',rt.STATE_PATH.with_name('telegram-health.json'))
    return live


def test_report_includes_manual_and_auto_and_actual_pnl(report):
    report.enter()
    b=report.broker
    manual=deepcopy(b.data['orders'][0]);manual.update(id='manual',number='manual',side='S',state='PENDING',filled=0)
    b.data['orders'].append(manual);b.data['pnl_vnd']=-240000.
    messages=tg.build_messages(b.snapshot(),rt.load_state())
    text='\n'.join(messages)
    assert 'Tự động' in text and 'Thủ công/ngoài bot' in text
    assert '-240.000' in text and 'khớp 0/1' in text and 'Chờ khớp' in text
    assert 'account-hash' not in text


def test_protective_child_is_auto(report):
    report.enter();s=report.broker.snapshot();c=s['conditions'][0]
    c.update(child='child',status='TRIGGERED')
    child=deepcopy(s['orders'][0]);child.update(id='child',number='child',side='S')
    s['orders'].append(child)
    assert 'child' in tg.automatic_ids(rt.load_state(),s)


def test_previous_day_ids_not_classified_as_auto(report):
    report.enter();s=report.broker.snapshot()
    s['checked_at']=(report.clock[0]+timedelta(days=1)).isoformat()
    assert not tg.automatic_ids(rt.load_state(),s)


def test_first_report_and_restart_dedup_no_trade(report):
    sent=[]
    assert tg.poll(sender=lambda m:sent.append(m) or True)[0]
    count=len(sent)
    report.clock[0]+=timedelta(seconds=31)
    assert tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert len(sent)==count and not report.broker.sent
    assert rt.load_state()['telegram_vps']['result']=='SENT'


def test_pnl_updates_periodically_not_every_tick(report):
    sent=[];send=lambda m:sent.append(m) or True
    tg.poll(sender=send);count=len(sent)
    report.broker.data['pnl_vnd']=-100000
    report.clock[0]+=timedelta(minutes=1)
    tg.poll(sender=send);assert len(sent)==count
    report.clock[0]+=timedelta(minutes=14)
    tg.poll(sender=send);assert len(sent)>count
    assert '-100.000' in sent[-1]


def test_fill_change_sends_without_waiting_15_minutes(report):
    report.enter();sent=[];send=lambda m:sent.append(m) or True
    tg.poll(sender=send);count=len(sent)
    report.broker.data['orders'][0]['state']='CANCELED'
    report.clock[0]+=timedelta(seconds=31)
    tg.poll(sender=send)
    assert len(sent)>count


def test_failed_snapshot_sends_nothing(report):
    report.broker.fail_read=True;sent=[]
    assert not tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert not sent


def test_reports_work_when_autotrade_disabled(report):
    report.cfg['enabled']=False;sent=[]
    assert tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert sent and not report.broker.sent


def test_account_changed_blocked(report):
    rt.tick(allow_actions=False)
    report.broker.data['account_ref']='wrong';sent=[]
    assert not tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert not sent


def test_failed_send_retries_after_5_minutes(report):
    assert not tg.poll(sender=lambda m:False)[0]
    report.clock[0]+=timedelta(minutes=1)
    sent=[];tg.poll(sender=lambda m:sent.append(m) or True)
    assert not sent
    report.clock[0]+=timedelta(minutes=4)
    assert tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert sent


def test_large_report_splits_and_escapes_html(report):
    report.enter();s=report.broker.snapshot();row=s['orders'][0]
    s['orders']=[{**row,'id':str(i),'number':str(i),'symbol':'<TEST&>'} for i in range(100)]
    parts=tg.build_messages(s,rt.load_state())
    assert len(parts)>1 and all(len(p)<3100 for p in parts)
    assert '&lt;TEST&amp;&gt;' in ''.join(parts) and '<TEST&>' not in ''.join(parts)
    assert '#99 ' in ''.join(parts)


def test_reserves_before_external_send(report):
    def send(message):
        assert rt.load_state()['telegram_vps']['result']=='UNKNOWN'
        return True
    assert tg.poll(sender=send)[0]


def test_after_close_report_only_once(report):
    report.clock[0]=report.clock[0].replace(hour=15)
    sent=[];send=lambda m:sent.append(m) or True
    tg.poll(sender=send);count=len(sent)
    report.clock[0]+=timedelta(minutes=16)
    tg.poll(sender=send)
    assert len(sent)==count


def test_missing_credentials_persist_without_broker_access(report,monkeypatch):
    monkeypatch.delenv('TELEGRAM_TOKEN',raising=False)
    monkeypatch.setattr(tg,'connect',lambda cfg:pytest.fail('must not read VPS'))
    assert not tg.poll()[0]
    assert tg.health()['phase']=='FAILED'
    assert 'TELEGRAM_TOKEN' in tg.health()['error']


def test_config_failure_is_caught_and_recovers(report,monkeypatch):
    from vn_invest import auto_trader as at
    original=at.load_config
    def broken():raise ValueError('secret-token-must-not-appear')
    monkeypatch.setattr(at,'load_config',broken)
    assert not tg.poll(sender=lambda m:True)[0]
    assert tg.health()['phase']=='FAILED'
    assert 'secret-token' not in tg.HEALTH_PATH.read_text(encoding="utf-8")
    monkeypatch.setattr(at,'load_config',original)
    assert tg.poll(sender=lambda m:True)[0]
    assert tg.health()['phase']=='SENT' and tg.health()['sent_at']
    assert tg.health()['error']==''


def test_snapshot_error_persisted(report):
    report.broker.fail_read=True
    assert not tg.poll(sender=lambda m:True)[0]
    assert tg.health()['phase']=='FAILED'
    assert 'account' not in tg.HEALTH_PATH.read_text(encoding="utf-8")


def test_disabled_reports_do_not_connect(report,monkeypatch):
    report.cfg['telegram_vps_reports']=False
    monkeypatch.setattr(tg,'connect',lambda cfg:pytest.fail('disabled'))
    assert not tg.poll(sender=lambda m:True)[0]
    assert tg.health()['phase']=='DISABLED'


def test_worker_polls_even_without_credentials_and_starts_once(report,monkeypatch):
    calls=[]
    class FakeThread:
        def __init__(self,**kwargs):self.target=kwargs['target']
        def start(self):calls.append('start')
        def is_alive(self):return True
    monkeypatch.setattr(tg,'_WORKER',None)
    monkeypatch.setattr(tg.threading,'Thread',FakeThread)
    monkeypatch.delenv('TELEGRAM_TOKEN',raising=False)
    monkeypatch.setattr(tg,'poll',lambda:calls.append('poll'))
    def stop(seconds):raise InterruptedError()
    monkeypatch.setattr(tg.time,'sleep',stop)
    tg.ensure_worker();tg.ensure_worker()
    with pytest.raises(InterruptedError):tg._WORKER.target()
    assert calls==['start','poll']


def test_app_starts_readonly_reporter_before_tab_selection():
    import ast
    from pathlib import Path
    tree=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text(encoding='utf-8'))
    assert any(isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
               and isinstance(n.value.func,ast.Name) and n.value.func.id=='ensure_vps_reports'
               for n in tree.body)


def test_report_waits_for_journal_lock_then_sends_once(report,monkeypatch):
    from contextlib import contextmanager
    original=rt.locked_state
    attempts=[]
    @contextmanager
    def busy():
        attempts.append(1)
        if len(attempts)<3:raise PermissionError('busy')
        with original() as state:yield state
    monkeypatch.setattr(rt,'locked_state',busy)
    sent=[]
    assert tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert len(sent)==1 and len(attempts)==5  # schedule read, reservation, result + two retries
    assert rt.load_state()['telegram_vps']['result']=='SENT'


def test_report_busy_timeout_does_not_send(report,monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def busy():
        raise PermissionError('busy')
        yield
    monkeypatch.setattr(rt,'locked_state',busy)
    sent=[]
    assert not tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert not sent and 'journal' in tg.health()['error']


def test_report_does_not_replay_body_on_permission_error(report):
    calls=[]
    with pytest.raises(PermissionError):
        with tg._report_state():
            calls.append(1)
            raise PermissionError('write failed')
    assert calls==[1]



def test_missing_protection_rejected_exit_and_overdue_are_visible(report):
    report.enter()
    state=rt.load_state();cycle=state['cycles'][0]
    cycle['exits']=[{'state':'REJECTED'}]
    report.broker.data['conditions']=[]
    report.clock[0]+=timedelta(minutes=40)
    text='\n'.join(tg.build_messages(report.broker.snapshot(),state))
    assert 'journal ghi REJECTED' in text
    assert 'chưa xác nhận lý do' in text
    assert 'sau hạn giữ' in text
    assert 'chưa xác nhận có bảo vệ' in text
    assert text.index('CẦN KIỂM TRA')<text.index('Vị thế hiện tại')
    assert len(report.broker.sent)==1


def test_deadline_warning_changes_fingerprint_without_order_change(report):
    report.enter();state=rt.load_state()
    before=tg.fingerprint(report.broker.snapshot(),state)
    report.clock[0]+=timedelta(minutes=31)
    after=tg.fingerprint(report.broker.snapshot(),state)
    assert after!=before
    report.clock[0]+=timedelta(seconds=30)
    assert tg.fingerprint(report.broker.snapshot(),state)==after


def test_rejected_exit_changes_fingerprint_without_broker_order(report):
    report.enter();state=rt.load_state();snapshot=report.broker.snapshot()
    before=tg.fingerprint(snapshot,state)
    state['cycles'][0]['exits']=[{'state':'REJECTED'}]
    assert tg.fingerprint(snapshot,state)!=before


def test_flat_account_does_not_raise_stale_position_alarm(report):
    report.enter();state=rt.load_state()
    state['cycles'][0]['exits']=[{'state':'REJECTED'}]
    report.broker.data['positions'][0]['net']=0
    report.broker.data['conditions']=[]
    report.clock[0]+=timedelta(hours=1)
    assert tg.risk_alerts(report.broker.snapshot(),state)==[]


def test_waiting_condition_is_not_claimed_to_guarantee_protection(report):
    report.enter()
    alerts=tg.risk_alerts(report.broker.snapshot(),rt.load_state())
    assert alerts==[]



def test_canceled_exit_is_not_reported_as_rejected(report):
    report.enter();state=rt.load_state()
    state['cycles'][0]['exits']=[{'state':'CANCELED','broker_id':'canceled'}]
    row=deepcopy(report.broker.data['orders'][0])
    row.update(id='canceled',side='S',state='CANCELED',filled=0)
    report.broker.data['orders'].append(row)
    text='\n'.join(tg.build_messages(report.broker.snapshot(),state))
    assert 'Đã hủy' in text
    assert 'REJECTED' not in text and 'bị từ chối' not in text


def test_rejection_requires_matching_broker_order_for_definitive_wording(report):
    report.enter();state=rt.load_state()
    state['cycles'][0]['exits']=[{'state':'REJECTED','broker_id':'reject'}]
    row=deepcopy(report.broker.data['orders'][0])
    row.update(id='reject',side='S',state='REJECTED',filled=0)
    report.broker.data['orders'].append(row)
    text='\n'.join(tg.build_messages(report.broker.snapshot(),state))
    assert 'sổ lệnh VPS xác nhận lệnh thoát bị từ chối' in text



def test_after_final_changes_and_restart_do_not_send_or_read_vps(report,monkeypatch):
    report.clock[0]=report.clock[0].replace(hour=15)
    sent=[];send=lambda m:sent.append(m) or True
    assert tg.poll(sender=send)[0]
    count=len(sent)
    report.clock[0]+=timedelta(minutes=1)
    report.broker.data['positions'][0]['net']=1
    monkeypatch.setattr(tg,'connect',lambda cfg:pytest.fail('after final must not read VPS'))
    assert tg.poll(sender=send)[0]
    assert len(sent)==count


@pytest.mark.parametrize('hour,days',[(7,0),(15,5),(10,6)])
def test_closed_hours_do_not_read_or_send(report,monkeypatch,hour,days):
    report.clock[0]=(report.clock[0]+timedelta(days=days)).replace(hour=hour)
    monkeypatch.setattr(tg,'connect',lambda cfg:pytest.fail('outside automatic hours'))
    sent=[]
    assert tg.poll(sender=lambda m:sent.append(m) or True)[0]
    assert not sent


def test_next_weekday_resumes_after_final(report):
    report.clock[0]=report.clock[0].replace(hour=15)
    sent=[];send=lambda m:sent.append(m) or True
    tg.poll(sender=send);count=len(sent)
    report.clock[0]=(report.clock[0]+timedelta(days=1)).replace(hour=9)
    assert tg.poll(sender=send)[0]
    assert len(sent)>count


def test_manual_report_after_final_still_works(report):
    report.clock[0]=report.clock[0].replace(hour=15)
    sent=[];send=lambda m:sent.append(m) or True
    tg.poll(sender=send);count=len(sent)
    report.clock[0]+=timedelta(minutes=1)
    assert tg.poll(force=True,sender=send)[0]
    assert len(sent)>count


def test_failed_final_retries_are_bounded_and_wait_five_minutes(report):
    report.clock[0]=report.clock[0].replace(hour=15)
    sent=[];send=lambda m:sent.append(m) or False
    assert not tg.poll(sender=send)[0]
    report.clock[0]+=timedelta(minutes=1)
    report.broker.data['positions'][0]['net']=1
    tg.poll(sender=send)
    assert len(sent)==1
    for _ in range(5):
        report.clock[0]+=timedelta(minutes=5)
        tg.poll(sender=send)
    assert len(sent)==3



@pytest.mark.parametrize('change',[
    {'subtype':'TP'}, {'side':'B'}, {'remaining':-1}, {'status':'PENDING_CANCEL'},
    {'qty':2,'remaining':2}, {'trigger':1999.}, {'remaining':'NaN'},
])
def test_pending_condition_does_not_hide_unprotected_position(report,change):
    report.enter()
    report.broker.data['conditions'][0].update(change)
    text='\n'.join(tg.build_messages(report.broker.snapshot(),rt.load_state()))
    assert 'chưa xác nhận đủ SL/Stop' in text


def test_partial_stop_coverage_warns(report):
    report.enter()
    report.broker.data['positions'][0]['net']=2
    text='\n'.join(tg.build_messages(report.broker.snapshot(),rt.load_state()))
    assert 'SL/Stop bảo vệ (1/2 HĐ)' in text


def test_duplicate_conditions_do_not_count_as_full_coverage(report):
    report.enter()
    report.broker.data['positions'][0]['net']=2
    report.broker.data['conditions'].append(deepcopy(report.broker.data['conditions'][0]))
    text='\n'.join(tg.build_messages(report.broker.snapshot(),rt.load_state()))
    assert 'chưa xác nhận đủ SL/Stop' in text
