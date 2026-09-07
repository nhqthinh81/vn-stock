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
