"""Read-only VPS Telegram reports, independent of order execution."""
from __future__ import annotations
import hashlib
import json
import os
import threading
import time
from datetime import datetime, time as dtime
from . import autotrade_runtime as rt
from .vps_broker import connect, now_vn
from .alerter import fmt_vn, tg_escape

_WORKER=None
_LOCK=threading.Lock()
_LAST_ERROR=''
STATES={'FILLED':'Đã khớp hết','PARTIAL':'Khớp một phần','PENDING':'Chờ khớp',
        'CANCELED':'Đã hủy','REJECTED':'Từ chối','UNKNOWN':'Chưa rõ'}


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


def report_lines(snapshot,state):
    stamp=datetime.fromisoformat(snapshot['checked_at'])
    auto=automatic_ids(state,snapshot)
    orders=sorted(snapshot['orders'],key=lambda o:(str(o.get('time','')),o['id']))
    lines=['<b>VPS · LỆNH THẬT &amp; LÃI/LỖ</b>',stamp.strftime('%d/%m/%Y %H:%M:%S'),
           '<b>Tổng lãi/lỗ tài khoản VPS (vm): '+fmt_vn(snapshot['pnl_vnd'],signed=True)+' đ</b>',
           'Theo số VPS trả về; không cộng PnL mô phỏng.',
           f'Sổ lệnh hôm nay: {len(orders)} · Bot: {sum(o["id"] in auto for o in orders)} · '
           f'Thủ công/ngoài bot: {sum(o["id"] not in auto for o in orders)}']
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


def poll(force=False,sender=None):
    global _LAST_ERROR
    from .auto_trader import load_config
    cfg=load_config()
    if not cfg.get('telegram_vps_reports',True):return False,'Báo cáo VPS đang tắt'
    try:
        with connect(cfg) as broker:snapshot=broker.snapshot()
        now=now_vn()
        with rt.locked_state() as state:
            if state['account_ref'] and state['account_ref']!=snapshot['account_ref']:
                raise ValueError('Tài khoản báo cáo khác tài khoản bot đã ghim')
            meta=state.setdefault('telegram_vps',{})
            key=fingerprint(snapshot,state)
            last=datetime.fromisoformat(meta['attempt_at']) if meta.get('attempt_at') else None
            elapsed=(now-last).total_seconds() if last else float('inf')
            in_day=now.weekday()<5 and dtime(8,45)<=now.time().replace(tzinfo=None)<=dtime(14,45)
            final=now.weekday()<5 and now.time().replace(tzinfo=None)>dtime(14,45) and meta.get('final_date')!=now.date().isoformat()
            due=(force or key!=meta.get('fingerprint') or final or
                 (in_day and elapsed>=900) or (meta.get('result')=='FAILED' and elapsed>=300))
            if not due or elapsed<30:return True,'Chưa đến lượt báo cáo'
            messages=build_messages(snapshot,state)
            # Reserve before external sends. A crash leaves UNKNOWN and no rapid
            # resend; Telegram has no idempotency key. Periodic reports continue.
            meta.update(fingerprint=key,attempt_at=now.isoformat(),result='UNKNOWN',parts=len(messages))
            if final:meta['final_date']=now.date().isoformat()
            rt.save_state(state)
        send=sender or send_message
        ok=True
        for message in messages:
            if not send(message):ok=False;break
        with rt.locked_state() as state:
            meta=state['telegram_vps']
            if meta.get('attempt_at')==now.isoformat():
                meta['result']='SENT' if ok else 'FAILED'
                if ok:meta['sent_at']=now.isoformat()
                rt.save_state(state)
        _LAST_ERROR='' if ok else 'Telegram chưa xác nhận nhận báo cáo; sẽ thử lại sau 5 phút'
        return ok,'Đã gửi báo cáo VPS' if ok else _LAST_ERROR
    except Exception as exc:
        _LAST_ERROR=f'Báo cáo VPS chưa gửi: {type(exc).__name__}'
        return False,_LAST_ERROR


def ensure_worker():
    global _WORKER
    with _LOCK:
        if _WORKER and _WORKER.is_alive():return
        def run():
            from .auto_trader import load_config
            while load_config().get('telegram_vps_reports',True):
                if os.getenv('TELEGRAM_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'):
                    poll()
                time.sleep(30)
        _WORKER=threading.Thread(target=run,name='VPS-Telegram-readonly',daemon=True)
        _WORKER.start()
