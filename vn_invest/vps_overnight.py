"""Verified end-of-day carry and next-session recovery. No direct broker API calls."""
from datetime import datetime, timedelta, time as dtime
from . import autotrade_runtime as rt
from .vps_broker import number


def _settled(intent,date):
    if intent.get('state')=='NOT_SENT':return True
    if intent.get('broker_id'):
        return intent.get('verified_date')==date and intent.get('state') in rt.TERMINAL
    rc=intent.get('response_rc')
    return intent.get('state')=='REJECTED' and type(rc) is int and rc<=0


def _signature(cycle):
    return [[x.get('id'),x.get('state'),x.get('broker_id'),x.get('filled',0),x.get('response_rc'),x.get('verified_date')]
            for x in [cycle['entry']]+cycle['exits']]


def checkpoint(cycle,snapshot,now):
    """Only a verified final-session observation can authorize the next carry."""
    if now.weekday()>=5 or now.time().replace(tzinfo=None)<dtime(14,45):return
    date=now.date().isoformat()
    if snapshot['checked_at'][:10]!=date:return
    carry=cycle.get('overnight')
    intents=cycle['exits'][carry['exit_start']:] if carry and carry['date']==date else [cycle['entry']]+cycle['exits']
    if not all(_settled(x,date) for x in intents):
        cycle['overnight_checkpoint_status']='Chưa đủ bằng chứng lệnh cũ đã kết thúc; chưa cho phép phục hồi Stop qua đêm'
        return
    pos=rt._position(snapshot,cycle['symbol'])
    if not pos['net']:return
    cycle['overnight_checkpoint_status']='Đã lưu mốc cuối phiên; đầu phiên sau vẫn phải đối soát lại VPS'
    cycle['overnight_checkpoint']={'date':date,'net':pos['net'],'due':pos['due'],
        'checked_at':snapshot['checked_at'],'ledger':_signature(cycle),
        'condition_ids':[c['id'] for c in snapshot['conditions'] if c['symbol']==cycle['symbol']]}


def _next_weekday(date):
    day=datetime.fromisoformat(date).date()+timedelta(days=1)
    while day.weekday()>=5:day+=timedelta(days=1)
    return day.isoformat()


def _current_net(carry,snapshot,symbol):
    from .vps_stop_guard import signed_fills
    pos=rt._position(snapshot,symbol)
    if snapshot['checked_at'][:10]!=carry['date']:raise ValueError('Sổ lệnh khác ngày nền qua đêm')
    if pos['net'] and (not pos['due'] or datetime.strptime(pos['due'],'%d/%m/%Y').date()<datetime.fromisoformat(snapshot['checked_at']).date()):
        raise ValueError('Hợp đồng hết hạn hoặc chưa xác minh hạn')
    if pos['net'] and pos['due']!=carry['due']:raise ValueError('Hạn hợp đồng đã thay đổi')
    expected=carry['net']+signed_fills(snapshot,symbol)
    if pos['net']!=expected or pos['net']*carry['net']<0 or abs(pos['net'])>abs(carry['net']):
        raise ValueError('Vị thế qua đêm chưa khớp nền cuối ngày và khớp hôm nay')
    return pos


def reconcile(state,cycle,broker,snapshot,cfg,now,allow_actions):
    from .vps_stop_guard import active_conditions, protection_coverage
    cycle.pop('overnight_ready_at',None)
    date=now.date().isoformat()
    def block(message):
        cycle['state']='UNKNOWN'
        state['last_error']='Qua đêm: '+message
        rt.save_state(state)
    stamp=datetime.fromisoformat(snapshot['checked_at'])
    if not 0<=(now-stamp).total_seconds()<=30 or stamp.date()!=now.date():
        block('snapshot cũ hoặc khác ngày; chờ đọc lại');return
    carry=cycle.get('overnight')
    if not carry or carry['date']!=date:
        saved=cycle.get('overnight_checkpoint')
        if not saved or _next_weekday(saved['date'])!=date:
            block('thiếu mốc cuối phiên liền trước đã xác minh; không tự nhận vị thế');return
        if saved['ledger']!=_signature(cycle):
            block('journal thay đổi sau mốc cuối ngày; cần đối soát');return
        carry={'date':date,'net':saved['net'],'due':saved['due'],'exit_start':len(cycle['exits']),
               'condition_ids':saved['condition_ids']}
        cycle['overnight']=carry
    try:pos=_current_net(carry,snapshot,cycle['symbol'])
    except ValueError as exc:block(str(exc));return
    rows={c['id']:c for c in snapshot['conditions']}
    if (set(cycle.get('condition_ids',[])) | set(carry['condition_ids']))-rows.keys():
        block('thiếu điều kiện cũ đã biết; không suy đoán đã hủy');return
    exits=cycle['exits'][carry['exit_start']:]
    for ex in exits:
        row=rt._match_intent(ex,snapshot['orders'],date)
        if ex.get('broker_id') and row is None:
            block('thiếu lệnh thoát đã biết trong ngày');return
    # Ordinary IDs are day-scoped. Historical entry/exits are never matched here.
    conditions=active_conditions(snapshot,cycle['symbol'])
    pending_guards=[g for g in state.get('normal_stops',[]) if g['state']!='CLOSED']
    coverage=protection_coverage(snapshot,pos)
    cycle['protection']={**coverage,'tp_qty':0,'tp_required':False,'invalid_branches':[]}
    cycle['state']='OVERNIGHT'
    if not pos['net']:
        if conditions or pending_guards or any(not _settled(x,date) for x in exits):
            block('vị thế phẳng nhưng lệnh còn chưa kết thúc; giữ khóa');return
        cycle.update(state='CLOSED',closed_at=now.isoformat())
        rt.save_state(state);return
    sl=number(cycle['sl'],'SL qua đêm');last=number(pos['last'],'giá VPS')
    if sl<=0 or last<=0:block('giá/SL không hợp lệ');return
    exit_required=(last<=sl if pos['net']>0 else last>=sl) or rt.day_state(state,now)['loss_latched']
    cycle['overnight_exit_required']=exit_required
    unresolved=[x for x in exits if not _settled(x,date)]
    if unresolved:
        ex=unresolved[0]
        if allow_actions and cfg.get('enabled') and not cfg.get('dry_run',True) and rt.continuous(now) and ex.get('broker_id') and ex['state'] not in rt.TERMINAL:
            age=(now-datetime.fromisoformat(ex['submitted_at'])).total_seconds()
            if age>=cfg.get('exit_timeout_seconds',30):rt._cancel(state,cycle,broker,'cancel_order',ex['broker_id'])
        block('yêu cầu thoát còn chờ/chưa rõ; không gửi thêm Stop hoặc lệnh thoát');return
    if exits and exits[-1]['state']=='REJECTED':
        block('yêu cầu thoát thất bại; cần đối chiếu SmartPro');return
    foreign_orders=[o for o in snapshot['orders'] if o['symbol']==cycle['symbol'] and o['state'] not in rt.TERMINAL]
    if foreign_orders:
        block('còn lệnh thường chờ khớp; không chồng thêm bảo vệ');return
    actions=allow_actions and cfg.get('enabled') and not cfg.get('dry_run',True) and rt.continuous(now)
    if all(g['state']=='PROTECTED' for g in pending_guards):
        checkpoint(cycle,snapshot,now)
    if pending_guards:
        cycle['overnight_ready_at']=snapshot['checked_at']
        rt.save_state(state);return  # Stop lifecycle is reconciled under the same lock.
    if conditions:
        if coverage['confirmed'] and not exit_required:
            state['last_error']=''
            rt.save_state(state);return
        own=[c for c in conditions if c['id'] in cycle.get('condition_ids',[])]
        if len(own)!=len(conditions) or any(c['status']=='TRIGGERED' for c in conditions):
            block('điều kiện ngoài bot hoặc đang kích hoạt; chờ đối soát');return
        if actions:
            c=own[0]
            if c['status']!='PENDING_CANCEL':rt._cancel(state,cycle,broker,'cancel_condition',c['id'],c['type'])
        state['last_error']='Qua đêm: chờ hủy nhánh cũ được bot quản lý trước khi phục hồi bảo vệ'
        rt.save_state(state);return
    if exit_required:
        state['last_error']='Qua đêm: đã chạm SL/trần lỗ; chờ thoát trong phiên liên tục'
        rt.save_state(state)
        if not actions:return
        fresh=broker.snapshot(datetime.fromisoformat(cycle['created_at']).date())
        if fresh['account_ref']!=snapshot['account_ref'] or _current_net(carry,fresh,cycle['symbol'])['net']!=pos['net'] or active_conditions(fresh,cycle['symbol']) or any(o['state'] not in rt.TERMINAL for o in fresh['orders']):
            block('trạng thái thay đổi trước khi thoát; đọc lại');return
        if not rt.continuous(rt.now_vn()):return
        import uuid
        intent={'id':uuid.uuid4().hex,'kind':'exit','symbol':cycle['symbol'],
                'side':'SHORT' if pos['net']>0 else 'LONG','qty':abs(pos['net']),
                'before_ids':[o['id'] for o in fresh['orders']],'filled':0}
        cycle['exits'].append(intent)
        rt._dispatch(state,broker,intent);return
    cycle['overnight_ready_at']=snapshot['checked_at']
    state['last_error']='Qua đêm: đã đối soát vị thế; chờ xác nhận Stop tại SL cũ'
    rt.save_state(state)
