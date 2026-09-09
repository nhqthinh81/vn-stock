"""Read-only VPS Telegram reports, independent of order execution."""
from __future__ import annotations
import hashlib
import json
import os
import threading
import time
from datetime import datetime, time as dtime
from contextlib import contextmanager, ExitStack
from . import autotrade_runtime as rt
from .vps_broker import connect, now_vn
from .alerter import fmt_vn, tg_escape

_WORKER=None
_LOCK=threading.Lock()
_LAST_ERROR=''
HEALTH_PATH=rt.runtime_dir()/'vps_telegram_health.json'  # local disk, not the synced repo tree
_HEALTH_LOCK=threading.Lock()
STATES={'FILLED':'Đã khớp hết','PARTIAL':'Khớp một phần','PENDING':'Chờ khớp',
        'CANCELED':'Đã hủy','REJECTED':'Từ chối','UNKNOWN':'Chưa rõ'}


def health():
    """Persistent diagnostics only: no account, message body, or credentials."""
    try:
        value=json.loads(HEALTH_PATH.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def _record_health(phase,error=''):
    global _LAST_ERROR
    _LAST_ERROR=error
    with _HEALTH_LOCK:
        value=health()
        value.update(checked_at=now_vn().isoformat(),phase=phase,error=error,pid=os.getpid())
        if phase=='SENT':value['sent_at']=value['checked_at']
        temporary=HEALTH_PATH.with_name(f'{HEALTH_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        try:
            HEALTH_PATH.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
            os.replace(temporary,HEALTH_PATH)
        except OSError:
            # Diagnostics must not prevent reporting; keep the safe error in RAM.
            _LAST_ERROR=error or 'Không ghi được trạng thái báo cáo Telegram'
        finally:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass


@contextmanager
def _report_state():
    # Another app process can hold the OS lock during broker reconciliation.
    # Retry acquisition only; never replay a body that may already have sent.
    deadline=time.monotonic()+30
    with ExitStack() as stack:
        while True:
            try:
                state=stack.enter_context(rt.locked_state())
                break
            except PermissionError:
                if time.monotonic()>=deadline:
                    raise TimeoutError('Journal is busy') from None
                time.sleep(0.25)
        yield state


def automatic_ids(state,snapshot):
    ids=set()
    for cycle in state['cycles']:
        # Broker normal order IDs can be reused on a later trading date.
        if cycle.get('created_at','')[:10]!=snapshot['checked_at'][:10]:
            continue
        parent=cycle['entry']
        ids.update(x['broker_id'] for x in [parent]+cycle['exits'] if x.get('broker_id'))
        for c in snapshot['conditions']:
            if c['type']=='sl_tp' and c['symbol']==cycle['symbol'] and parent.get('broker_id') and (
                c['parent']==parent['broker_id'] or c['parent_number']==parent.get('broker_number')):
                if c['child'] not in ('','null','undefined'):
                    ids.add(c['child'])
    for guard in state.get('normal_stops', []):
        if guard.get('created_at','')[:10]!=snapshot['checked_at'][:10]:
            continue
        if guard.get('child_id'):
            ids.add(guard['child_id'])
        for condition in snapshot['conditions']:
            if condition['id']==guard['intent'].get('condition_id') and condition['child'] not in ('','null','undefined'):
                ids.add(condition['child'])
    return ids


def risk_alerts(snapshot,state):
    """Stable warning text from fresh positions and durable execution state."""
    positions=[p for p in snapshot['positions'] if p['net']]
    if not positions:return []
    alerts=[]
    now=datetime.fromisoformat(snapshot['checked_at'])
    for p in positions:
        waiting=[c for c in snapshot['conditions'] if c['symbol']==p['symbol']
                 and c['status'] not in rt.COND_TERMINAL and c['status']!='TRIGGERED']
        if not waiting:
            alerts.append(f"{p['symbol']}: không thấy lệnh điều kiện còn chờ; chưa xác nhận có bảo vệ.")
        else:
            from .vps_stop_guard import protection_coverage
            coverage=protection_coverage(snapshot,p)
            if not coverage['confirmed']:
                alerts.append(f"{p['symbol']}: chưa xác nhận đủ SL/Stop bảo vệ ({coverage['sl_qty']}/{coverage['required_qty']} HĐ); có lệnh chờ không đồng nghĩa đã bảo vệ.")
        for cycle in state['cycles']:
            if cycle['state']=='CLOSED' or cycle['symbol']!=p['symbol']:continue
            start=cycle.get('overnight',{}).get('exit_start',0)
            rejected=[e for e in cycle.get('exits',[])[start:] if e.get('state')=='REJECTED']
            if rejected:
                confirmed=any(e.get('broker_id') and any(
                    o['id']==e['broker_id'] and o['state']=='REJECTED'
                    for o in snapshot['orders']) for e in rejected)
                if confirmed:
                    alerts.append(f"{p['symbol']}: sổ lệnh VPS xác nhận lệnh thoát bị từ chối; vị thế vẫn mở.")
                else:
                    alerts.append(f"{p['symbol']}: journal ghi REJECTED cho yêu cầu thoát; chưa xác nhận lý do trên sổ VPS. Vị thế vẫn mở; cần đối chiếu SmartPro.")
            if not cycle.get('overnight') and cycle.get('exit_at') and now>=datetime.fromisoformat(cycle['exit_at']):
                alerts.append(f"{p['symbol']}: vị thế vẫn mở sau hạn giữ của bot.")
    return alerts


def report_lines(snapshot,state):
    stamp=datetime.fromisoformat(snapshot['checked_at'])
    auto=automatic_ids(state,snapshot)
    orders=sorted(snapshot['orders'],key=lambda o:(str(o.get('time','')),o['id']))
    lines=['<b>VPS · LỆNH THẬT &amp; LÃI/LỖ</b>',stamp.strftime('%d/%m/%Y %H:%M:%S'),
           '<b>Tổng lãi/lỗ tài khoản VPS (vm): '+fmt_vn(snapshot['pnl_vnd'],signed=True)+' đ</b>',
           'Theo số VPS trả về; không cộng PnL mô phỏng.',
           f'Sổ lệnh hôm nay: {len(orders)} · Bot: {sum(o["id"] in auto for o in orders)} · '
           f'Thủ công/ngoài bot: {sum(o["id"] not in auto for o in orders)}']
    alerts=risk_alerts(snapshot,state)
    if alerts:
        lines.append('<b>⚠️ CẦN KIỂM TRA VỊ THẾ THẬT</b>')
        lines.extend(tg_escape(a) for a in alerts)
    positions=[p for p in snapshot['positions'] if p['net']]
    lines.append('<b>Vị thế hiện tại</b>')
    if not positions:lines.append('Không có vị thế mở trên VPS.')
    for p in positions:
        lines.append(tg_escape(f'{p["symbol"]}: {"LONG" if p["net"]>0 else "SHORT"} {abs(p["net"])} HĐ · giá vốn {fmt_vn(p["avg"],1)}'))
    lines.append('<b>Lệnh trong sổ VPS</b>')
    for o in orders:
        origin='Tự động' if o['id'] in auto else 'Thủ công/ngoài bot'
        side={'B':'LONG','S':'SHORT'}.get(o['side'],o['side'])
        lines.append(tg_escape(f'#{o["number"]} {o.get("time", "")} · {origin} · {o["symbol"]} {side} '
                     f'· đặt {o["qty"]} @{o["price"]} · khớp {o["filled"]}/{o["qty"]}'
                     +(f' @{fmt_vn(o["avg"],1)}' if o['filled'] else '')
                     +f' · {STATES.get(o["state"],o["state"])}'))
    if not orders:lines.append('Chưa có lệnh trong sổ VPS hôm nay.')
    waiting=[c for c in snapshot['conditions'] if c['status'] not in rt.COND_TERMINAL and c['status']!='TRIGGERED']
    lines.append(f'<b>Lệnh điều kiện còn chờ: {len(waiting)}</b> (chưa đồng nghĩa đã vào sàn)')
    own_stops={g['intent'].get('condition_id') for g in state.get('normal_stops',[])}
    for c in waiting:
        origin='Stop bảo vệ tự động' if c['id'] in own_stops else 'Lệnh điều kiện'
        lines.append(tg_escape(f'{origin} #{c["id"]} {c["symbol"]} {c["type"]}/{c["subtype"]} · '
                              f'KL {c["qty"]} · ngưỡng {c["trigger"]} · {c["status"]}'))
    lines.append('Thủ công/ngoài bot = chưa ghép được với journal bot; có thể gồm lệnh từ công cụ khác.')
    return lines


def build_messages(snapshot,state):
    # Split at line boundaries; never split an HTML entity/tag.
    parts=[];current=''
    for line in report_lines(snapshot,state):
        if len(line)>3000:raise ValueError('Dòng báo cáo VPS quá dài')
        if len(current)+len(line)+1>3000:
            parts.append(current);current=''
        current+=("\n" if current else '')+line
    if current:parts.append(current)
    return [f'<b>VPS {i+1}/{len(parts)}</b>\n'+part for i,part in enumerate(parts)]


def fingerprint(snapshot,state):
    # Price/PnL ticks do not cause a message every 30 seconds. Order/fill changes do.
    ids=automatic_ids(state,snapshot)
    data={'account':snapshot['account_ref'],'date':snapshot['checked_at'][:10],
          'risk_alerts':risk_alerts(snapshot,state),
          'orders':sorted((o['id'],o['state'],o['qty'],o['filled'],o['avg'],str(o['price']),o['id'] in ids)
                          for o in snapshot['orders']),
          'conditions':sorted((c['id'],c['status'],c['order_status'],str(c['remaining']),str(c['trigger']))
                              for c in snapshot['conditions']),
          'positions':sorted((p['symbol'],p['net']) for p in snapshot['positions'])}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def send_message(message):
    # Never log exceptions containing the token-bearing request URL.
    import requests
    token=os.getenv('TELEGRAM_TOKEN','');chat=os.getenv('TELEGRAM_CHAT_ID','')
    if not token or not chat:return False
    try:
        r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id':chat,'text':message,'parse_mode':'HTML'},timeout=10)
        return r.ok and r.json().get('ok') is True
    except Exception:
        return False


def automatic_paused(meta,now):
    clock=now.time().replace(tzinfo=None)
    if now.weekday()>=5 or clock<dtime(8,45):return True
    if clock<=dtime(14,45):return False
    if meta.get('final_date')!=now.date().isoformat():return False
    if meta.get('result') in ('SENT','UNKNOWN'):return True
    if meta.get('final_attempts',0)>=3:return True
    last=datetime.fromisoformat(meta['attempt_at']) if meta.get('attempt_at') else None
    return last is not None and (now-last).total_seconds()<300


def poll(force=False,sender=None):
    global _LAST_ERROR
    from .auto_trader import load_config
    stage='đọc cấu hình'
    try:
        cfg=load_config()
        if not cfg.get('telegram_vps_reports',True):
            _record_health('DISABLED')
            return False,'Báo cáo VPS đang tắt'
        if not force:
            stage='đọc lịch báo cáo trong journal'
            with _report_state() as state:
                if automatic_paused(state.get('telegram_vps',{}),now_vn()):
                    _record_health('AFTER_HOURS')
                    return True,'Ngoài lịch báo cáo tự động hoặc đã chốt báo cáo cuối phiên'
        if sender is None:
            missing=[name for name in ('TELEGRAM_TOKEN','TELEGRAM_CHAT_ID') if not os.getenv(name,'').strip()]
            if missing:
                _record_health('FAILED','Thiếu cấu hình: '+', '.join(missing))
                return False,_LAST_ERROR
        stage='đọc phiên VPS'
        _record_health('READING_VPS')
        with connect(cfg) as broker:snapshot=broker.snapshot()
        now=now_vn()
        stage='chờ/ghi journal'
        _record_health('WAITING_JOURNAL')
        with _report_state() as state:
            if state['account_ref'] and state['account_ref']!=snapshot['account_ref']:
                raise ValueError('Tài khoản báo cáo khác tài khoản bot đã ghim')
            meta=state.setdefault('telegram_vps',{})
            if not force and automatic_paused(meta,now):
                _record_health('AFTER_HOURS')
                return True,'Đã chốt báo cáo cuối phiên hoặc đang chờ lượt thử lại'
            key=fingerprint(snapshot,state)
            last=datetime.fromisoformat(meta['attempt_at']) if meta.get('attempt_at') else None
            elapsed=(now-last).total_seconds() if last else float('inf')
            in_day=now.weekday()<5 and dtime(8,45)<=now.time().replace(tzinfo=None)<=dtime(14,45)
            final=now.weekday()<5 and now.time().replace(tzinfo=None)>dtime(14,45) and meta.get('final_date')!=now.date().isoformat()
            due=(force or key!=meta.get('fingerprint') or final or
                 (in_day and elapsed>=900) or (meta.get('result')=='FAILED' and elapsed>=300))
            if not due or elapsed<30:
                if meta.get('result')=='FAILED':
                    _record_health('FAILED','Telegram chưa xác nhận nhận báo cáo; sẽ thử lại sau 5 phút')
                else:_record_health('WAITING')
                return True,'Chưa đến lượt báo cáo'
            messages=build_messages(snapshot,state)
            # Reserve before external sends. A crash leaves UNKNOWN and no rapid
            # resend; Telegram has no idempotency key. Periodic reports continue.
            meta.update(fingerprint=key,attempt_at=now.isoformat(),result='UNKNOWN',parts=len(messages))
            if now.weekday()<5 and now.time().replace(tzinfo=None)>dtime(14,45):
                attempts=meta.get('final_attempts',0) if meta.get('final_date')==now.date().isoformat() else 0
                meta.update(final_date=now.date().isoformat(),final_attempts=attempts+1)
            rt.save_state(state)
        stage='gửi Telegram'
        _record_health('SENDING')
        send=sender or send_message
        ok=True
        for message in messages:
            if not send(message):ok=False;break
        stage='ghi kết quả gửi vào journal'
        with _report_state() as state:
            meta=state['telegram_vps']
            if meta.get('attempt_at')==now.isoformat():
                meta['result']='SENT' if ok else 'FAILED'
                if ok:meta['sent_at']=now.isoformat()
                rt.save_state(state)
        _record_health('SENT' if ok else 'FAILED',
                       '' if ok else 'Telegram chưa xác nhận nhận báo cáo; sẽ thử lại sau 5 phút')
        return ok,'Đã gửi báo cáo VPS' if ok else _LAST_ERROR
    except Exception as exc:
        _record_health('FAILED',f'Báo cáo VPS chưa hoàn tất ({stage}): {type(exc).__name__}')
        return False,_LAST_ERROR


def ensure_worker():
    global _WORKER
    with _LOCK:
        if _WORKER and _WORKER.is_alive():return
        def run():
            # poll handles configuration/read failures, so the thread survives
            # and records missing credentials instead of silently skipping work.
            while True:
                poll()
                time.sleep(30)
        _WORKER=threading.Thread(target=run,name='VPS-Telegram-readonly',daemon=True)
        _WORKER.start()
