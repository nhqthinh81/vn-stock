"""Stop orders protecting filled regular positions; never an entry strategy.

Called under the live runtime's process/file lock. Stop intents and cancellation
results share its durable journal. This module does not submit ordinary exits.
"""
from __future__ import annotations
import math
import threading
import uuid
from datetime import datetime
from .vps_broker import VN, number
from . import autotrade_runtime as rt

_ATR=None
_ATR_LOCK=threading.Lock()


def observe_closed_bars(bars,symbol):
    """Publish ATR only from closed, finite bars; do not import the UI in a worker."""
    global _ATR
    if bars is None or len(bars)<20:return
    stamp=bars.index[-1].to_pydatetime()
    stamp=stamp.replace(tzinfo=VN) if stamp.tzinfo is None else stamp.astimezone(VN)
    with _ATR_LOCK:
        if _ATR and _ATR['symbol']==symbol and _ATR['at']==stamp:return
    import numpy as np
    import pandas_ta as ta
    values=bars[['High','Low','Close']].to_numpy()
    if not np.isfinite(values).all() or (values<=0).any():return
    if (bars['High']<bars['Low']).any():return
    atrs=ta.atr(bars['High'],bars['Low'],bars['Close'],length=14)
    if atrs is None or not math.isfinite(float(atrs.iloc[-1])) or atrs.iloc[-1]<=0:return
    with _ATR_LOCK:
        _ATR={'symbol':symbol,'at':stamp,'atr':float(atrs.iloc[-1]),'close':float(bars['Close'].iloc[-1])}


def stop_distance(symbol,last,now,cfg):
    with _ATR_LOCK:quote=dict(_ATR) if _ATR else None
    if not quote or quote['symbol']!=symbol or not 0<=(now-quote['at']).total_seconds()<=120:
        raise ValueError('Chưa có ATR từ nến đóng mới để đặt Stop bảo vệ')
    if not math.isfinite(quote['atr']) or quote['atr']<=0 or quote['close']<=0:
        raise ValueError('ATR bảo vệ không hợp lệ')
    if abs(last-quote['close'])/quote['close']*100>cfg.get('max_price_drift_pct',3):
        raise ValueError('Giá VPS lệch nguồn ATR')
    return max(3*quote['atr'],1.)


def active_conditions(snapshot,symbol):
    return [c for c in snapshot['conditions'] if c['symbol']==symbol and
            c['status'] not in rt.COND_TERMINAL and not
            (c['status']=='TRIGGERED' and c['order_status'] in ('Filled','Canceled','Rejected','Expired'))]


def protection_coverage(snapshot,position):
    """Read-only SL coverage; an unrelated pending order/TP is not protection."""
    net=position['net'];required=abs(net);covered=0;seen=set();invalid=False
    if not net:return dict(confirmed=True,sl_qty=0,required_qty=0)
    for c in snapshot['conditions']:
        if c['symbol']!=position['symbol'] or c['status']!='PENDING_TRIGGER':continue
        if not (c['type']=='stop' or c['type']=='sl_tp' and c.get('subtype')=='SL'):continue
        try:
            qty=number(c['qty'],'qty');left=number(c['remaining'],'remaining')
            trigger=number(c['trigger'],'trigger');last=number(position['last'],'last')
            valid=(c['id'] not in seen and qty>0 and qty.is_integer() and left.is_integer()
                   and 0<left<=qty and c['side']==('S' if net>0 else 'B')
                   and last>0 and trigger>0 and (trigger<last if net>0 else trigger>last))
            if c['type']=='stop':
                valid=valid and c.get('relation')==('LTEQ' if net>0 else 'GTEQ') and c.get('price_type')=='MTL'
            seen.add(c['id'])
            if valid:covered+=int(left)
            else:invalid=True
        except (ValueError,KeyError,TypeError):invalid=True
    return dict(confirmed=not invalid and covered==required,sl_qty=covered,required_qty=required)


def signed_fills(snapshot,symbol):
    total=0
    for o in snapshot['orders']:
        if o['symbol']!=symbol:continue
        if o['side'] not in ('B','S'):raise ValueError('Chiều sổ khớp không hợp lệ')
        total+=o['filled']*(1 if o['side']=='B' else -1)
    return total


def closing_orders(snapshot,symbol,net,exclude=None):
    side='S' if net>0 else 'B'
    return [o for o in snapshot['orders'] if o['symbol']==symbol and o['id']!=exclude
            and o['side']==side and o['state'] not in rt.TERMINAL]


def note(state,message):
    state['stop_guard_message']=message
    rt.save_state(state)


def same_stop(row,intent):
    try:
        return (row['type']=='stop' and row['symbol']==intent['symbol']
                and row['side']==('S' if intent['side']=='SHORT' else 'B')
                and number(row['qty'],'KL Stop')==intent['qty']
                and abs(number(row['trigger'],'ngưỡng Stop')-intent['trigger'])<0.001
                and row.get('relation')==intent['relation'] and row.get('price_type')=='MTL')
    except (ValueError,TypeError,KeyError):return False


def cancel(state,guard,broker,kind,target,allow_actions):
    guard['state']='CANCELING'
    rt.save_state(state)
    if allow_actions:
        rt._cancel(state,guard,broker,kind,target,'stop' if kind=='cancel_condition' else None)


def reconcile_stops(state,broker,snapshot,cfg,now,allow_actions=True):
    """Reconcile owned Stops even when creation is disabled. Never adopt a Stop
    submitted manually. Unknown submissions keep the gate closed across restart.
    """
    records=state.setdefault('normal_stops',[])
    pending=[g for g in records if g['state']!='CLOSED']
    if len(pending)>1:raise ValueError('Nhiều Stop bảo vệ chưa đối soát xong')
    actions=allow_actions and cfg.get('enabled') and not cfg.get('dry_run',True)
    owner=rt.active_cycle(state)
    if owner and owner['created_at'][:10]<now.date().isoformat() and owner.get('overnight_ready_at')!=snapshot['checked_at']:
        actions=False
    if pending:
        g=pending[0];intent=g['intent'];symbol=intent['symbol']
        pos=rt._position(snapshot,symbol);net=pos['net']
        if g.get('child_id') and g.get('child_date')!=now.date().isoformat():
            g['state']='UNKNOWN';note(state,'Lệnh con Stop thuộc ngày cũ/chưa có ngày xác minh; không ghép ID sang phiên mới');return
        if intent.get('condition_id'):
            matches=[c for c in snapshot['conditions'] if c['id']==intent['condition_id']]
        else:
            matches=[c for c in snapshot['conditions'] if c['id'] not in intent['before_ids'] and same_stop(c,intent)]
        if not matches and not intent.get('condition_id') and intent['state']=='REJECTED':
            g['state']='REJECTED';note(state,'VPS từ chối Stop bảo vệ; cần xử lý trên SmartPro');return
        if len(matches)!=1:
            if not matches and not intent.get('condition_id') and intent['state']=='NOT_SENT':
                g['state']='CLOSED';note(state,'Stop chưa gửi; sẽ xét lại từ vị thế mới');return
            g['state']='UNKNOWN'
            note(state,'Chưa đối soát được duy nhất một Stop; không gửi lại hoặc mở thêm');return
        row=matches[0]
        intent['condition_id']=row['id']
        intent['broker_status']=row['status']
        cycle=rt.active_cycle(state)
        if (cycle and cycle.get('overnight_exit_required') and cycle.get('overnight_ready_at')==snapshot['checked_at']
                and row['status']=='PENDING_TRIGGER'):
            cancel(state,g,broker,'cancel_condition',row['id'],actions and rt.continuous(now))
            note(state,'Qua đêm: chờ hủy Stop trước khi thoát do SL/trần lỗ');return
        if row['status']=='TRIGGERED':
            children=[o for o in snapshot['orders'] if o['id']==row['child'] or o['number']==row['child_number']]
            if len(children)!=1:
                g['state']='UNKNOWN';note(state,'Stop kích hoạt: chờ mã lệnh con VPS');return
            child=children[0]
            if child['side']!=('S' if intent['side']=='SHORT' else 'B') or child['symbol']!=symbol or child['qty']!=intent['qty']:
                g['state']='UNKNOWN';note(state,'Lệnh con Stop không khớp phiếu bảo vệ');return
            if child['filled']<g.get('child_filled',0):
                g['state']='UNKNOWN';note(state,'Khối lượng khớp Stop lùi; giữ khóa đối soát');return
            g.update(child_id=child['id'],child_date=now.date().isoformat(),child_filled=child['filled'],state='TRIGGERED')
            sign=1 if g['net']>0 else -1
            expected=g['net']-sign*child['filled']
            if child['state'] not in rt.TERMINAL:
                if net*sign<=0 or abs(net)<child['qty']-child['filled'] or closing_orders(snapshot,symbol,g['net'],child['id']):
                    cancel(state,g,broker,'cancel_order',child['id'],actions)
                note(state,'Stop đã kích hoạt; theo dõi/hủy phần lệnh con còn chờ, không gửi lệnh đóng thứ hai');return
            if net!=expected and signed_fills(snapshot,symbol)!=net:
                g['state']='UNKNOWN';note(state,'Vị thế chưa đồng bộ với khớp Stop; giữ khóa');return
            # A terminal rejection is not repaired by endless immediate triggers.
            if child['state']=='REJECTED':
                g['state']='REJECTED';note(state,'Lệnh con Stop bị từ chối; cần xử lý trên SmartPro');return
            g['state']='CLOSED';note(state,'Đã đối soát lệnh con Stop; sẽ xét bảo vệ phần vị thế còn lại');return
        if row['status'] in rt.COND_TERMINAL:
            if g.get('child_id'):
                g['state']='UNKNOWN';note(state,'Trạng thái Stop đã kích hoạt thay đổi; cần đối soát lệnh con');return
            g['state']='CLOSED';note(state,'Đã xác nhận Stop hết hiệu lực/hủy');return
        foreign=[c for c in active_conditions(snapshot,symbol) if c['id']!=row['id']]
        changed=(net!=g['net'] or pos['avg']!=g['avg'] or not same_stop(row,intent))
        if changed or foreign or closing_orders(snapshot,symbol,g['net']):
            cancel(state,g,broker,'cancel_condition',row['id'],actions)
            note(state,'Vị thế/bảo vệ đã thay đổi; chờ hủy Stop cũ trước khi thay thế');return
        if row['status']=='PENDING_TRIGGER' and protection_coverage(snapshot,pos)['confirmed']:
            g['state']='PROTECTED';note(state,'Stop bảo vệ đã xác nhận trên VPS')
        else:
            g['state']='UNKNOWN';note(state,'CẢNH BÁO: chưa xác nhận Stop bảo vệ đủ khối lượng/ngưỡng/trạng thái; tiếp tục đối soát')
        return

    if not cfg.get('normal_stop_enabled',True):
        note(state,'Tạo Stop bảo vệ lệnh thường đang tắt');return
    cycle=rt.active_cycle(state)
    carry=cycle.get('overnight') if cycle and cycle.get('overnight_ready_at')==snapshot['checked_at'] else None
    if cycle and not carry:
        coverage=protection_coverage(snapshot,rt._position(snapshot,cycle['symbol']))
        if coverage['confirmed']:
            note(state,'Vị thế bot: SL/Stop đã xác minh đủ khối lượng; tiếp tục đối soát chu kỳ')
        else:
            note(state,f"CẢNH BÁO: vị thế bot chưa xác nhận đủ SL/Stop ({coverage['sl_qty']}/{coverage['required_qty']} HĐ); không tự chồng Stop khi chu kỳ chưa đối soát xong")
        return
    symbol=cycle['symbol'] if carry else cfg['symbol_code']
    pos=rt._position(snapshot,symbol);net=pos['net']
    if not net:
        note(state,'Không có vị thế lệnh thường cần bảo vệ');return
    if active_conditions(snapshot,symbol):
        note(state,'Đã có lệnh điều kiện trên mã này; không chồng thêm Stop');return
    if closing_orders(snapshot,symbol,net):
        note(state,'Đang có lệnh ngược chiều chờ khớp; chưa đặt thêm Stop');return
    if signed_fills(snapshot,symbol)+(carry['net'] if carry else 0)!=net:
        note(state,'Sổ khớp trong ngày chưa khớp vị thế; chưa thể tạo Stop');return
    if not rt.continuous(now):
        note(state,'Chờ phiên liên tục để tạo Stop MTL bảo vệ');return
    if pos['avg']<=0 or pos['last']<=0 or not pos['due'] or datetime.strptime(pos['due'],'%d/%m/%Y').date()<now.date():
        note(state,'Chưa xác minh giá vốn/giá/hạn hợp đồng để bảo vệ');return
    if carry:
        trigger=round(number(cycle['sl'],'SL qua đêm'),1)
    else:
        try:distance=stop_distance(symbol,pos['last'],now,cfg)
        except ValueError as exc:
            note(state,str(exc));return
        trigger=round(pos['avg']-distance if net>0 else pos['avg']+distance,1)
    reference=pos['last'] if carry else pos['avg']
    if trigger<=0 or (net>0 and trigger>=reference) or (net<0 and trigger<=reference):
        note(state,'Ngưỡng Stop sau làm tròn không hợp lệ');return
    if not actions:
        note(state,'Đã tính được Stop; chế độ chỉ đọc/tắt gửi thật không tạo lệnh');return
    # Re-read immediately before reserving: don't submit using a prior position
    # snapshot while a manual close/protection has appeared in the meantime.
    fresh=broker.snapshot(datetime.fromisoformat(cycle['created_at']).date()) if carry else broker.snapshot()
    newpos=rt._position(fresh,symbol)
    if (fresh['account_ref']!=snapshot['account_ref'] or newpos['net']!=net or newpos['avg']!=pos['avg']
            or signed_fills(fresh,symbol)+(carry['net'] if carry else 0)!=net or active_conditions(fresh,symbol) or closing_orders(fresh,symbol,net)
            or carry and (any(o['symbol']==symbol and o['state'] not in rt.TERMINAL for o in fresh['orders']) or fresh['checked_at'][:10]!=carry['date'] or newpos['due']!=carry['due'] or newpos['last']<=0 or (newpos['last']<=trigger if net>0 else newpos['last']>=trigger))):
        note(state,'Vị thế/sổ lệnh thay đổi trước khi gửi Stop; xét lại ở lần đối soát sau');return
    intent={'id':uuid.uuid4().hex,'kind':'protect_stop','symbol':symbol,'side':'SHORT' if net>0 else 'LONG',
            'qty':abs(net),'trigger':trigger,'relation':'LTEQ' if net>0 else 'GTEQ',
            'before_ids':[c['id'] for c in fresh['conditions']]}
    g={'created_at':now.isoformat(),'state':'SUBMITTING','net':net,'avg':pos['avg'],
       'intent':intent,'cancels':{}}
    records.append(g)
    rt._dispatch(state,broker,intent)
    g['state']='UNKNOWN' if intent['state'] in ('ACK','UNKNOWN') else intent['state']
    note(state,'Đã lưu phiếu Stop; chờ xác nhận từ sổ điều kiện VPS')
