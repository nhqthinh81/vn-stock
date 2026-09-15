"""Durable AutoTrade lifecycle: one managed position, broker reconciliation first.

The file lock spans read/decide/save/send so separate Streamlit processes cannot
submit the same intent. UNKNOWN is persisted before every mutating broker call.
"""
from __future__ import annotations
import json
import math
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from .vps_broker import connect, now_vn, VN, number

_OLD_DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


def runtime_dir() -> Path:
    """Durable autotrade files live on LOCAL disk, never a synced drive.

    The repo tree here sits on a Google Drive virtual filesystem where
    os.replace is not atomic: concurrent sync activity concatenates the
    pre-image tail onto a fresh short write, so save_state() output kept coming
    back as `Extra data` JSON. Falls back to the in-repo data/ dir off Windows
    or when no local app-data path is known. Override with VNINVEST_RUNTIME_DIR.
    """
    base = os.environ.get('VNINVEST_RUNTIME_DIR') or (
        os.environ.get('LOCALAPPDATA') if os.name == 'nt' else None)
    return Path(base) / 'VNInvest' / 'runtime' if base else _OLD_DATA_DIR


_DEFAULT_STATE_PATH = runtime_dir() / 'autotrade_live_state.json'
STATE_PATH = _DEFAULT_STATE_PATH
_THREAD_LOCK = threading.Lock()
# Khoá file liên tiến trình: worker đối soát ở MỖI server Streamlit giữ khoá
# suốt 1 tick (nối CDP + đọc VPS, vài giây). Khoá không chờ mà không thử lại
# thì submit()/request_close() đụng đúng lúc đó là PermissionError → bỏ lệnh
# thật (11/09/2026 09:03). Chờ có giới hạn, bằng timeout của _THREAD_LOCK.
_FILE_LOCK_TIMEOUT_SEC = 30.0
_FILE_LOCK_POLL_SEC = 0.2
_FILE_LOCK_SLOW_SEC = 1.0    # chờ lâu hơn mức này thì ghi log kèm bên giữ khoá
_SLOW_LOG_EVERY_SEC = 60.0   # tiết chế log chờ khoá
_last_slow_log = 0.0
_WORKER_THREAD_NAME = 'VPS-reconcile'
_WORKER_LOCK = threading.Lock()
_WORKER = None
_LAST_ERROR = ''
_LAST_SNAPSHOT_AT = ''
_UNMANAGED_POSITIONS = ()
_MODULE_LOADED_AT = now_vn().isoformat()
_READ_ONLY_SENTINEL = Path(__file__).resolve().parent.parent / '.vninvest-read-only'
_WATCHED_SOURCES = tuple(Path(__file__).resolve().parent / name for name in (
    'auto_trader.py', 'autotrade_runtime.py', 'vps_broker.py',
    'vps_stop_guard.py', 'vps_telegram.py'))
_SOURCE_MTIME_AT_LOAD = max((path.stat().st_mtime for path in _WATCHED_SOURCES),default=0)


def _read_only_mode() -> bool:
    """Process-wide kill switch for every live broker mutation path."""
    return (_READ_ONLY_SENTINEL.exists() or
            os.environ.get('VNINVEST_READ_ONLY','').strip().lower() in ('1','true','yes','on'))
TERMINAL = {'FILLED', 'CANCELED', 'REJECTED', 'NOT_SENT'}
COND_TERMINAL = {'CANCELED', 'EXPIRED', 'REJECTED'}


class StateBusyError(RuntimeError):
    """Bounded journal lock acquisition timed out; safe to retry next poll."""


def _migrate_state_off_synced_drive():
    """One-time copy of a still-valid journal from the old in-repo data/ dir."""
    old = _OLD_DATA_DIR / 'autotrade_live_state.json'
    try:
        data = json.loads(old.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return  # missing or already corrupted on the synced drive — start fresh
    if isinstance(data, dict) and data.get('version') == 1 and isinstance(data.get('cycles'), list):
        save_state(data)


def load_state():
    if not STATE_PATH.exists() and STATE_PATH == _DEFAULT_STATE_PATH and runtime_dir() != _OLD_DATA_DIR:
        _migrate_state_off_synced_drive()
    if not STATE_PATH.exists():
        days = {}
        legacy = STATE_PATH.with_name('autotrade_state.json')
        if legacy.exists():
            with legacy.open(encoding='utf-8') as f:
                old = json.load(f)
            if old.get('date'):
                days[old['date']] = {'attempts':int(old.get('count',0)),
                                     'loss_latched':False,'pnl_vnd':None}
        return {'version':1, 'account_ref':None, 'days':days, 'cycles':[], 'last_error':''}
    with STATE_PATH.open(encoding='utf-8') as f:
        state = json.load(f)
    if state.get('version') != 1 or not isinstance(state.get('cycles'),list) or not isinstance(state.get('days'),dict):
        raise ValueError('Sổ AutoTrade không hợp lệ; không được tự tạo lại')
    return state


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True,exist_ok=True)
    tmp = STATE_PATH.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(state,f,ensure_ascii=False,allow_nan=False,indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,STATE_PATH)


def _lock_holder(handle) -> str:
    """Nhãn bên giữ khoá gần nhất (pid:thread) ghi từ byte 1 của file .lock —
    byte 0 là byte bị khoá, không đọc."""
    try:
        handle.seek(1)
        return handle.read(200).decode('utf-8', 'replace').strip() or 'không rõ'
    except OSError:
        return 'không đọc được'


def _stamp_lock_holder(handle) -> None:
    try:
        handle.seek(1)
        handle.write(f'{os.getpid()}:{threading.current_thread().name}'.encode('utf-8'))
        handle.truncate()
        handle.flush()
    except OSError:
        pass


def _worker_alive(name: str = _WORKER_THREAD_NAME) -> bool:
    """Streamlit nạp lại module khi file nguồn đổi → `_WORKER` toàn cục về None
    dù thread cũ vẫn chạy; nhìn theo TÊN thread thì không sinh worker thứ hai."""
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


@contextmanager
def locked_state():
    if not _THREAD_LOCK.acquire(timeout=30):
        raise StateBusyError('AutoTrade đang xử lý/đối soát một lệnh khác')
    handle = None
    acquired = False
    try:
        STATE_PATH.parent.mkdir(parents=True,exist_ok=True)
        lock_path = str(STATE_PATH)+'.lock'
        if not os.path.exists(lock_path):
            open(lock_path,'ab').close()
        # 'r+b' chứ KHÔNG 'a+b': append mode ép mọi write về cuối file, seek(1)
        # bị bỏ qua → nhãn pid:thread nối đuôi vô hạn (thấy thật 11/09 13:13).
        handle = open(lock_path,'r+b')
        if handle.seek(0,os.SEEK_END) == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        t0 = time.monotonic()
        deadline = t0 + _FILE_LOCK_TIMEOUT_SEC
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError as exc:   # Windows: PermissionError errno 13 khi tiến trình khác giữ khoá
                if time.monotonic() >= deadline:
                    raise StateBusyError(
                        f'Không giành được khoá trạng thái AutoTrade sau {_FILE_LOCK_TIMEOUT_SEC:g}s '
                        f'— tiến trình khác (server Streamlit thứ hai?) đang giữ '
                        f'[{_lock_holder(handle)}]: {exc}') from exc
                time.sleep(_FILE_LOCK_POLL_SEC)
        acquired = True
        waited = time.monotonic() - t0
        global _last_slow_log
        if waited >= _FILE_LOCK_SLOW_SEC and time.monotonic() - _last_slow_log >= _SLOW_LOG_EVERY_SEC:
            # Ai giữ khoá lâu? Ghi ra để lần sau không phải đoán (xem lessons 35).
            # Tiết chế 1 dòng/phút: vps_telegram thử lại mỗi 0,25s nên không tiết
            # chế là 500 dòng/3 phút (thấy thật 11/09 13:20).
            _last_slow_log = time.monotonic()
            try:
                from .auto_trader import _log
                _log(f'Chờ khoá trạng thái {waited:.1f}s — bên giữ trước đó: [{_lock_holder(handle)}]')
            except Exception:
                pass
        _stamp_lock_holder(handle)
        yield load_state()
    finally:
        if handle:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_UN)
            handle.close()
        _THREAD_LOCK.release()


def continuous(now):
    return now.weekday() < 5 and (dtime(9) <= now.time().replace(tzinfo=None) < dtime(11,30)
                                 or dtime(13) <= now.time().replace(tzinfo=None) < dtime(14,30))


def entry_session(now):
    return continuous(now) and (now.hour < 12 and now.time().replace(tzinfo=None) < dtime(11,25)
                               or now.hour >= 13 and now.time().replace(tzinfo=None) < dtime(14,25))


def active_cycle(state):
    active = [c for c in state['cycles'] if c['state'] != 'CLOSED']
    if len(active) > 1:
        raise ValueError('Sổ có nhiều vị thế chưa đối soát xong')
    return active[0] if active else None


def day_state(state, now):
    return state['days'].setdefault(now.date().isoformat(),{'attempts':0,'loss_latched':False,'pnl_vnd':None})


def update_risk(state,snapshot,cfg,now):
    if state['account_ref'] and state['account_ref'] != snapshot['account_ref']:
        raise ValueError('Tài khoản khác tài khoản AutoTrade đã ghim — không giao dịch')
    state['account_ref'] = snapshot['account_ref']
    day = day_state(state,now)
    day['pnl_vnd'] = snapshot['pnl_vnd']
    cap = int(cfg.get('max_daily_loss_vnd',0) or 0)
    if cap > 0 and snapshot['pnl_vnd'] <= -cap:
        day['loss_latched'] = True
        day.setdefault('locked_at',now.isoformat())
        day.setdefault('cap_vnd',cap)
    state['last_checked'] = now.isoformat()
    state['last_error'] = ''
    save_state(state)
    return day


def _position(snapshot,symbol):
    rows = [p for p in snapshot['positions'] if p['symbol']==symbol]
    if len(rows)>1:
        raise ValueError('VPS trả nhiều dòng vị thế cùng mã')
    return rows[0] if rows else {'symbol':symbol,'net':0,'last':0,'avg':0,'due':None}


def _match_intent(intent,orders,trade_date=None):
    if trade_date and intent.get('submitted_at','')[:10]!=trade_date:
        return None
    if intent.get('broker_id'):
        matches = [o for o in orders if o['id']==intent['broker_id']]
    else:
        matches = [o for o in orders if o['id'] not in intent['before_ids']
                   and o['symbol']==intent['symbol'] and o['side']==('B' if intent['side']=='LONG' else 'S')
                   and o['qty']==intent['qty']]
        if intent['kind']=='entry':
            def same_price(o):
                try: return abs(float(o['price'])-intent['price'])<0.001
                except (ValueError,TypeError): return False
            matches = [o for o in matches if same_price(o)]
    if len(matches)>1:
        raise ValueError('Nhiều lệnh VPS cùng khớp phiếu — giữ khóa chờ đối soát')
    if not matches:
        return None
    o=matches[0]
    if o['filled'] < intent.get('filled',0):
        raise ValueError('Khối lượng khớp VPS lùi so với sổ đã lưu')
    intent.update(broker_id=o['id'],broker_number=o['number'],state=o['state'],
                  filled=o['filled'],avg=o['avg'])
    if trade_date:intent['verified_date']=trade_date
    return o


def _managed_conditions(cycle,snapshot):
    entry = cycle['entry']
    if not entry.get('broker_id'):
        return []
    return [c for c in snapshot['conditions'] if c['type']=='sl_tp' and c.get('subtype') in ('SL','TP') and c['symbol']==cycle['symbol']
            and (c['parent']==entry['broker_id'] or c['parent_number']==entry.get('broker_number'))]



def protection_status(cycle, managed, remaining):
    """Count SL and TP separately: each branch must protect all remaining fills.

    VPS may create a pair per partial fill. Root rows are not protection.
    A branch with wrong side/trigger/quantity is never counted as confirmed.
    """
    coverage={'SL':0,'TP':0}
    invalid=[]
    expected_side='S' if cycle['side']=='LONG' else 'B'
    for c in managed:
        if c['status'] in COND_TERMINAL or c['status']=='TRIGGERED':
            continue
        if c['status']!='PENDING_TRIGGER':
            continue
        subtype=c['subtype']
        try:
            qty=number(c['qty'],'KL điều kiện')
            left=number(c['remaining'],'KL điều kiện còn lại')
            trigger=number(c['trigger'],'giá kích hoạt')
            # VPS reports REMAIN_QTY = 0 while the branch is still
            # PENDING_TRIGGER — the child order does not exist yet, so the whole
            # qty is armed. See vps_stop_guard.protection_coverage for the
            # incident this caused.
            armed=qty if left==0 else left
            expected=cycle['sl'] if subtype=='SL' else cycle.get('tp')
            valid=(qty>0 and qty.is_integer() and left.is_integer() and 0<=left<=qty
                   and armed>0 and c.get('side')==expected_side and expected is not None
                   and abs(trigger-expected)<0.001)
        except (ValueError,KeyError,TypeError):
            valid=False
        if not valid:
            invalid.append(subtype)
        else:
            coverage[subtype]+=int(armed)
    required=['SL']+(['TP'] if cycle.get('tp') is not None else [])
    confirmed=(not invalid and all(coverage[k]==remaining for k in required)
               and (cycle.get('tp') is not None or coverage['TP']==0))
    return {'confirmed':confirmed,'sl_qty':coverage['SL'],'tp_qty':coverage['TP'],
            'required_qty':remaining,'tp_required':cycle.get('tp') is not None,
            'invalid_branches':invalid}


def _dispatch(state,broker,intent):
    if _read_only_mode():
        raise RuntimeError('READ_ONLY đang bật; broker mutation bị chặn tại _dispatch')
    from .auto_trader import load_config
    current = load_config()
    if (not current.get('enabled') or current.get('dry_run', True)
            or intent['kind']=='entry' and not entry_session(now_vn())
            or intent['kind']=='exit' and not continuous(now_vn())
            or intent['kind']=='protect_stop' and (not current.get('normal_stop_enabled',True)
                                                  or not continuous(now_vn()))):
        intent['state']='NOT_SENT'
        intent['submitted_at']=now_vn().isoformat()
        save_state(state)
        return
    # Persist first; crashing before/after fetch is always recoverable as UNKNOWN.
    intent['state']='UNKNOWN'
    intent['submitted_at']=now_vn().isoformat()
    save_state(state)
    reply=broker.send(intent['kind'],intent,state['account_ref'])
    intent['state']=reply.get('outcome','UNKNOWN')
    if type(reply.get('response_rc')) is int:
        intent['response_rc']=reply['response_rc']  # numeric protocol status only, no server body
    if intent['state'] not in ('ACK','REJECTED','UNKNOWN'):
        intent['state']='UNKNOWN'
    save_state(state)


def _cancel(state,cycle,broker,kind,target,typ=None):
    key=kind+':'+target
    if kind=='cancel_order':
        legacy=cycle['cancels'].get(key)
        date=now_vn().date().isoformat()
        if not legacy or legacy.get('submitted_at','')[:10]!=date:
            key=kind+':'+date+':'+target
    previous=cycle['cancels'].get(key)
    if previous:
        if previous['state']=='NOT_SENT':
            _dispatch(state,broker,previous)
            return
        if previous['state']=='REJECTED':
            state['last_error']='VPS từ chối hủy lệnh; chờ xử lý trên SmartPro'
        elif previous['state']=='UNKNOWN':
            state['last_error']='Chưa rõ kết quả hủy; đang đối soát, không gửi hủy trùng'
        save_state(state)
        return
    intent={'id':uuid.uuid4().hex,'kind':kind,'target':target,'type':typ}
    cycle['cancels'][key]=intent
    _dispatch(state,broker,intent)


def _release_after_outside_close(state,cycle,broker,snapshot,cfg,now,managed,closing_rows,remaining,allow_actions):
    """VPS is flat because someone closed the bot position outside the bot.

    Release only with proof: filled opposite-side orders placed after the entry,
    not owned by the bot, add up exactly to what the bot still held. Portfolio
    lag (net=0 with no such fill) keeps the lock. Bot SL/TP left armed would open
    a new position when triggered, so they are cancelled first; no exit is sent.
    """
    entry=cycle['entry']
    owned=set(entry['before_ids'])|{entry.get('broker_id')}|set(closing_rows)|{
        x.get('broker_id') for x in cycle['exits']}
    close_side='S' if cycle['side']=='LONG' else 'B'
    outside=[o for o in snapshot['orders'] if o['id'] not in owned and o['symbol']==cycle['symbol']
             and o['side']==close_side and o['filled']>0]
    if sum(o['filled'] for o in outside)!=remaining:
        return False
    if any(x['state'] not in TERMINAL for x in cycle['exits']) or any(
            c['status']=='TRIGGERED' for c in managed):
        return False
    cycle['outside_close_ids']=sorted(o['id'] for o in outside)
    cycle['close_reason']='Vị thế đã đóng ngoài bot (lệnh thủ công)'
    armed=[c for c in managed if c['status'] not in COND_TERMINAL]
    if armed:
        cycle['state']='CLOSING'
        state['last_error']='Vị thế đã đóng tay; hủy SL/TP còn treo của bot trước khi nhả khóa'
        save_state(state)
        if allow_actions and cfg.get('enabled') and not cfg.get('dry_run',True):
            c=armed[0]
            if c['status']!='PENDING_CANCEL':
                _cancel(state,cycle,broker,'cancel_condition',c['id'],c['type'])
        return True
    cycle['state']='CLOSED'; cycle['closed_at']=now.isoformat()
    save_state(state)
    return True


def reconcile(state,broker,snapshot,cfg,now,allow_actions=True):
    day=update_risk(state,snapshot,cfg,now)
    cycle=active_cycle(state)
    if not cycle:
        return
    cycle.pop('recovery_ready_at',None)
    if cycle.get('recovery_stop_attempt'):
        from .vps_stop_guard import reconcile_recovery_cycle
        reconcile_recovery_cycle(state,cycle,snapshot,now)
        return
    from . import vps_overnight as overnight
    if cycle['created_at'][:10]<now.date().isoformat():
        overnight.reconcile(state,cycle,broker,snapshot,cfg,now,allow_actions)
        return
    entry=cycle['entry']
    entry_row=_match_intent(entry,snapshot['orders'],snapshot['checked_at'][:10])
    for ex in cycle['exits']:
        row = _match_intent(ex,snapshot['orders'],snapshot['checked_at'][:10])
        if ex.get('broker_id') and row is None:
            state['last_error']='Thiếu lệnh thoát đã biết; giữ khóa đối soát'
            save_state(state); return
    managed=_managed_conditions(cycle,snapshot)
    known_conditions=set(cycle.get('condition_ids',[]))
    visible_conditions={c['id'] for c in snapshot['conditions']}
    if known_conditions-visible_conditions:
        state['last_error']='Thiếu lệnh điều kiện đã biết trong kết quả VPS; giữ khóa đối soát'
        save_state(state); return
    cycle['condition_ids']=sorted(known_conditions | {c['id'] for c in managed})
    pos=_position(snapshot,cycle['symbol'])
    net=pos['net']
    sign=1 if cycle['side']=='LONG' else -1
    # Never close a different direction or a position larger than the fills we own.
    filled=entry.get('filled',0)
    if net and (net*sign<0 or abs(net)>filled):
        cycle['state']='UNKNOWN'
        state['last_error']='Vị thế VPS chưa khớp khối lượng bot đã xác nhận; không tự đóng/mở thêm'
        save_state(state); return
    # Portfolio and order endpoints are not atomic. Confirmed closing fills
    # reduce what we own even when the portfolio still shows the old position.
    closing_rows = {x['broker_id']: x.get('filled',0) for x in cycle['exits']
                    if x.get('broker_id')}
    known_closing=cycle.setdefault('protection_fills',{})
    visible_children=set()
    for condition in managed:
        if condition['status'] != 'TRIGGERED':
            continue
        child = next((o for o in snapshot['orders'] if
            o['id']==condition['child'] or o['number']==condition['child_number']),None)
        if child is None or child['id']==entry.get('broker_id'):
            state['last_error']='Chưa xác minh được lệnh con SL/TP; chờ đối soát'
            save_state(state); return
        if child['symbol']!=cycle['symbol'] or child['side']!=('S' if sign==1 else 'B'):
            raise ValueError('Lệnh con SL/TP không khớp vị thế quản lý')
        if child['filled']<known_closing.get(child['id'],0):
            raise ValueError('Khối lượng khớp SL/TP lùi so với sổ đã lưu')
        known_closing[child['id']]=child['filled']
        visible_children.add(child['id'])
        closing_rows[child['id']]=child['filled']
    if set(known_closing)-visible_children:
        state['last_error']='Thiếu kết quả lệnh con SL/TP đã biết; giữ khóa đối soát'
        save_state(state); return
    remaining=filled-sum(closing_rows.values())
    if net==0 and remaining>0 and entry.get('state') in TERMINAL:
        if _release_after_outside_close(state,cycle,broker,snapshot,cfg,now,managed,
                                        closing_rows,remaining,allow_actions):
            return
    if remaining<0 or abs(net)!=remaining:
        cycle['state']='UNKNOWN'
        state['last_error']='Vị thế chưa đồng bộ với lệnh đóng đã khớp; không đóng trùng'
        save_state(state); return
    if entry.get('broker_id') is None:
        if entry['state'] in ('REJECTED','NOT_SENT') and not net:
            cycle['state']='CLOSED'
        else:
            cycle['state']='UNKNOWN'
            state['last_error']='Chưa thấy mã lệnh mở: giữ khóa và tiếp tục đối soát'
        save_state(state); return
    if entry_row is None:
        state['last_error']='Không thấy lệnh đã biết trong sổ hiện tại; không suy đoán đã hủy'
        save_state(state); return
    entry_done=entry['state'] in TERMINAL
    waiting_exits=[x for x in cycle['exits'] if x['state'] not in TERMINAL]
    active_conditions=[c for c in managed if c['status'] not in COND_TERMINAL and c['status']!='TRIGGERED']
    triggered=[c for c in managed if c['status']=='TRIGGERED' and c['order_status'] not in ('Filled','Canceled','Rejected','Expired')]
    pending_parent=not entry_done
    age=(now-datetime.fromisoformat(entry['submitted_at'])).total_seconds()
    if pending_parent and age >= cfg.get('entry_timeout_seconds',60):
        cycle.setdefault('close_reason','Lệnh mở chờ quá lâu; hủy phần chưa khớp')
    if day['loss_latched']:
        cycle['close_reason']='Chạm trần lỗ ngày'
    if now >= datetime.fromisoformat(cycle['exit_at']):
        cycle.setdefault('close_reason','Hết thời gian giữ/đến giờ đóng phiên')
    if net and pos['last']>0:
        hit_sl=pos['last']<=cycle['sl'] if sign==1 else pos['last']>=cycle['sl']
        hit_tp=cycle.get('tp') is not None and (pos['last']>=cycle['tp'] if sign==1 else pos['last']<=cycle['tp'])
        if hit_sl or hit_tp:
            cycle.setdefault('close_reason','Chạm SL/TP theo vị thế thật')
    cycle['protection']=protection_status(cycle,managed,remaining)
    if any(c['status']=='TRIGGERED' for c in managed):
        cycle.setdefault('close_reason','SL/TP đã kích hoạt; đối soát các nhánh còn lại')
    # Require every configured branch, including all partial fills. A lone SL
    # must not masquerade as a complete SL/TP pair. Do not open again to repair it.
    if net and not cycle['protection']['confirmed']:
        cycle.setdefault('protection_unconfirmed_at',now.isoformat())
        missing_age=(now-datetime.fromisoformat(cycle['protection_unconfirmed_at'])).total_seconds()
        if missing_age>=cfg.get('protection_timeout_seconds',30):
            cycle.setdefault('close_reason','SL/TP thiếu hoặc sai ngưỡng/KL; thoát vị thế sau khi hủy bảo vệ')
    else:
        cycle.pop('protection_unconfirmed_at',None)
    if net==0 and entry_done:
        cycle.setdefault('close_reason','Vị thế VPS đã phẳng')
    overnight.checkpoint(cycle,snapshot,now)
    closing=bool(cycle.get('close_reason'))
    cycle['state']='CLOSING' if closing else ('OPEN' if filled else 'OPENING')
    save_state(state)
    if net==0 and entry_done and not cycle['exits']:
        cycle['state']='CLOSED'; cycle['closed_at']=now.isoformat()
        save_state(state)
        return
    if not allow_actions or not cfg.get('enabled') or cfg.get('dry_run',True):
        return
    if closing:
        # Cancellation must be observed from the server before sending an opposite
        # order; otherwise an SL trigger and a manual close could reverse the position.
        if pending_parent:
            _cancel(state,cycle,broker,'cancel_order',entry['broker_id']); return
        if active_conditions:
            c=active_conditions[0]
            if c['status']!='PENDING_CANCEL':
                _cancel(state,cycle,broker,'cancel_condition',c['id'],c['type'])
            return
        if triggered:
            child = next((o for o in snapshot['orders'] if any(
                c['child']==o['id'] or c['child_number']==o['number'] for c in triggered)),None)
            if child and child['state'] not in TERMINAL:
                _cancel(state,cycle,broker,'cancel_order',child['id'])
            else:
                state['last_error']='SL/TP đã kích hoạt; chờ xác nhận lệnh con trước khi đóng thêm'
                save_state(state)
            return
        if waiting_exits:
            ex=waiting_exits[0]
            age_exit=(now-datetime.fromisoformat(ex['submitted_at'])).total_seconds()
            if ex.get('broker_id') and age_exit>=cfg.get('exit_timeout_seconds',30):
                _cancel(state,cycle,broker,'cancel_order',ex['broker_id'])
            return
        if net==0:
            cycle['state']='CLOSED'; cycle['closed_at']=now.isoformat()
            save_state(state); return
        if cycle['exits'] and cycle['exits'][-1]['state']=='REJECTED':
            cycle['recovery_ready_at']=snapshot['checked_at']
            state['last_error']='Journal ghi REJECTED cho yêu cầu thoát; cần đối chiếu trạng thái và lý do trên SmartPro'
            save_state(state); return
        if not continuous(now):
            state['last_error']='Còn vị thế; chờ phiên liên tục để gửi MTL (không gửi MTL vào ATC)'
            save_state(state); return
        intent={'id':uuid.uuid4().hex,'kind':'exit','symbol':cycle['symbol'],
                'side':'SHORT' if sign==1 else 'LONG','qty':abs(net),
                'before_ids':[o['id'] for o in snapshot['orders']],'filled':0}
        cycle['exits'].append(intent)
        _dispatch(state,broker,intent)


def _deadline(now,hold_minutes):
    cutoff=now.replace(hour=11 if now.hour<12 else 14,minute=29,second=0,microsecond=0)
    return min(now+timedelta(minutes=hold_minutes),cutoff).isoformat()


def submit(side,strong,price,in_session,sl_price,tp_price,signal_id=None,signal_at=None):
    if _read_only_mode():
        return False,'READ_ONLY đang bật; không gửi lệnh mở'
    from .auto_trader import load_config
    cfg=load_config(); now=now_vn()
    if not cfg.get('enabled'):
        return False,'AutoTrade đang tắt'
    if cfg.get('dry_run',True):
        return False,'Chế độ dry-run không đi qua luồng gửi thật'
    if not (strong or cfg.get('auto_all_signals')):
        return False,'Tín hiệu thường chưa được phép gửi thật'
    if not in_session or not entry_session(now):
        return False,'Ngoài cửa sổ mở lệnh tự động'
    if side not in ('LONG','SHORT'):
        return False,'Chiều lệnh không hợp lệ'
    for label,value in [('giá',price),('SL',sl_price)]+([('TP',tp_price)] if tp_price is not None else []):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            return False,f'{label} phải là số hữu hạn dương'
    sign=1 if side=='LONG' else -1
    if sign*(price-sl_price)<=0 or (tp_price is not None and sign*(tp_price-price)<=0):
        return False,'SL/TP sai chiều so với giá vào'
    price,sl_price=round(price,1),round(sl_price,1)
    tp_price=round(tp_price,1) if tp_price is not None else None
    if sign*(price-sl_price)<=0 or (tp_price is not None and sign*(tp_price-price)<=0):
        return False,'SL/TP trùng giá vào sau khi làm tròn bước giá'
    max_qty=cfg.get('max_qty',1)
    if type(max_qty) is not int or max_qty<=0:
        return False,'max_qty không hợp lệ'
    qty=1 if strong else max_qty
    try:
        stamp=datetime.fromisoformat(signal_at) if signal_at else now
    except (TypeError,ValueError):
        return False,'Thời gian tín hiệu không hợp lệ'
    stamp=stamp.replace(tzinfo=VN) if stamp.tzinfo is None else stamp.astimezone(VN)
    # signal_at is when the closed-bar signal became available, not bar open.
    age=(now-stamp).total_seconds()
    max_age=cfg.get('max_signal_age_seconds',120)
    if not -5 <= age <= max_age:
        return False,f'Tín hiệu đã cũ hoặc chưa hiệu lực; không gửi bù (tuổi {age:.1f}s, giới hạn {max_age}s)'
    key=signal_id or f'{stamp:%Y-%m-%dT%H:%M}:{side}:{price:.1f}'
    try:
        with locked_state() as state, connect(cfg) as broker:
            snapshot=broker.snapshot()
            now=now_vn()
            age=(now-stamp).total_seconds()
            if not entry_session(now) or not -5 <= age <= max_age:
                return False,f'Tín hiệu/phiên đã hết hiệu lực trong lúc chờ (tuổi {age:.1f}s, giới hạn {max_age}s)'
            reconcile(state,broker,snapshot,cfg,now,allow_actions=False)
            if any(c['signal_id']==key for c in state['cycles']):
                return False,'Tín hiệu đã xử lý — không gửi trùng'
            if active_cycle(state):
                return False,'Đang có vị thế/lệnh chưa đối soát xong'
            if any(g['state']!='CLOSED' for g in state.get('normal_stops',[])):
                return False,'Stop bảo vệ lệnh thường chưa đối soát xong'
            day=day_state(state,now)
            if day['loss_latched']:
                return False,'Đã khóa mở lệnh tới hết ngày do chạm trần lỗ VPS'
            if day['attempts']>=int(cfg.get('max_orders_per_day',6)):
                return False,'Chạm trần lệnh/ngày'
            if any(p['net'] for p in snapshot['positions']):
                return False,'VPS đang có vị thế thật; không mở chồng'
            if any(o['state'] not in TERMINAL for o in snapshot['orders']):
                return False,'VPS còn lệnh chờ/chưa rõ; không mở thêm'
            if any(c['status'] not in COND_TERMINAL and not (c['status']=='TRIGGERED' and c['order_status'] in ('Filled','Canceled','Rejected','Expired')) for c in snapshot['conditions']):
                return False,'VPS còn lệnh điều kiện có thể kích hoạt; không mở thêm'
            pos=_position(snapshot,cfg['symbol_code'])
            if not pos['due'] or datetime.strptime(pos['due'],'%d/%m/%Y').date()<now.date():
                return False,'Chưa xác minh được hạn hợp đồng trên VPS'
            if pos['last']<=0 or abs(pos['last']-price)/price*100>cfg.get('max_price_drift_pct',3):
                return False,'Giá tín hiệu lệch giá VPS hoặc không có giá hợp lệ'
            if sign*(pos['last']-sl_price)<=0 or (tp_price is not None and sign*(tp_price-pos['last'])<=0):
                return False,'Giá VPS đã chạm/vượt SL/TP của tín hiệu; không mở lệnh muộn'
            # Nominal loss at the limit-entry price; stop slippage/fees can add loss.
            from .auto_trader import _PT_VALUE_VND
            cap=int(cfg.get('max_daily_loss_vnd',0) or 0)
            if cap>0:
                budget=max(0.,min(float(cap),cap+snapshot['pnl_vnd']))
                stop_loss=round(abs(price-sl_price)*qty*_PT_VALUE_VND,2)
                if stop_loss>budget:
                    return False,(f'Rủi ro tới SL {stop_loss:,.0f}đ vượt ngân sách lỗ còn lại '
                                  f'{budget:,.0f}đ; chưa gồm phí/trượt giá — không mở lệnh')
            intent={'id':uuid.uuid4().hex,'kind':'entry','symbol':cfg['symbol_code'],'side':side,
                    'qty':qty,'price':round(price,1),'sl':round(sl_price,1),
                    'tp':round(tp_price,1) if tp_price is not None else None,
                    'before_ids':[o['id'] for o in snapshot['orders']],'filled':0}
            cycle={'signal_id':key,'signal_at':stamp.isoformat(),'symbol':intent['symbol'],'side':side,'state':'OPENING',
                   'sl':intent['sl'],'tp':intent['tp'],'exit_at':_deadline(now,30),
                   'entry':intent,'exits':[],'cancels':{},'created_at':now.isoformat()}
            state['cycles'].append(cycle)
            day['attempts']+=1  # reserve before send; timeout/restart cannot reset the cap
            _dispatch(state,broker,intent)
            # Acknowledgement is not a fill. The worker continues after this bounded wait.
            deadline=time.monotonic()+float(cfg.get('submit_timeout_seconds',20))
            while time.monotonic()<deadline:
                snap=broker.snapshot()
                reconcile(state,broker,snap,cfg,now_vn(),allow_actions=False)
                if intent.get('broker_id') or intent['state'] in ('REJECTED','NOT_SENT'):
                    break
                time.sleep(1)
            if intent['state'] in ('REJECTED','NOT_SENT'):
                return False,'VPS từ chối hoặc cấu hình đã dừng trước khi gửi lệnh mở'
            return bool(intent.get('broker_id')),f"Lệnh mở: {intent['state']} — đã lưu để tiếp tục đối soát"
    except Exception as exc:
        return False,f'AutoTrade giữ an toàn: {type(exc).__name__}: {str(exc)[:160]}'


def request_close(side,qty=1,price=None,signal_id=None):
    # Persist the request even if VPS is temporarily disconnected. Do not derive
    # a close from a virtual position that this runtime never opened.
    if _read_only_mode():
        return False,'READ_ONLY đang bật; không gửi yêu cầu đóng/hủy bảo vệ'
    from .auto_trader import load_config
    cfg=load_config()
    if not cfg.get('enabled') or cfg.get('dry_run',True):
        return False,'AutoTrade chưa bật gửi thật'
    if side not in ('LONG','SHORT') or type(qty) is not int or qty<=0:
        return False,'Chiều/KL đóng không hợp lệ'
    try:
        with locked_state() as state:
            cycle=active_cycle(state)
            if not cycle or cycle['side']!=side or (signal_id and cycle['signal_id']!=signal_id):
                return False,'Không có vị thế thật do bot quản lý khớp yêu cầu đóng'
            if qty!=cycle['entry']['qty']:
                return False,'Yêu cầu đóng phải khớp toàn bộ KL của vị thế bot quản lý'
            cycle.setdefault('close_reason','Engine yêu cầu đóng vị thế')
            save_state(state)
        return True,'Đã lưu yêu cầu đóng; worker đối soát/hủy SL-TP trước khi thoát'
    except Exception as exc:
        return False,f'Chưa lưu được yêu cầu đóng: {type(exc).__name__}'


def tick(allow_actions=True):
    global _LAST_ERROR, _LAST_SNAPSHOT_AT, _UNMANAGED_POSITIONS
    allow_actions = bool(allow_actions) and not _read_only_mode()
    from .auto_trader import load_config
    cfg=load_config()
    try:
        with locked_state() as state, connect(cfg) as broker:
            cycle=active_cycle(state)
            since=datetime.fromisoformat(cycle['created_at']).date() if cycle else None
            from .vps_stop_guard import reconcile_stops
            pending_stops=[g for g in state.get('normal_stops',[]) if g['state']!='CLOSED']
            if pending_stops:
                first=min(datetime.fromisoformat(g['created_at']).date() for g in pending_stops)
                since=min(since,first) if since else first
            snap=broker.snapshot(since)
            _LAST_SNAPSHOT_AT=snap['checked_at']
            _UNMANAGED_POSITIONS=tuple({
                'symbol':p['symbol'],'net':p['net'],'avg':p['avg'],'last':p['last'],'due':p['due']}
                for p in snap['positions'] if p['net']) if not cycle else ()
            reconcile(state,broker,snap,cfg,now_vn(),allow_actions)
            reconcile_stops(state,broker,snap,cfg,now_vn(),allow_actions)
            _LAST_ERROR=''
            return True,'Đã đối soát với VPS'
    except Exception as exc:
        # Do not recreate corrupt state or release unresolved intents on any error.
        _LAST_ERROR=f'Đối soát chưa hoàn tất: {type(exc).__name__}: {str(exc)[:160]}'
        return False,_LAST_ERROR


def status():
    state=load_state(); day=state['days'].get(now_vn().date().isoformat(),{})
    cycle=active_cycle(state)
    unmanaged=[dict(p) for p in _UNMANAGED_POSITIONS]
    restart_required=any(path.stat().st_mtime>_SOURCE_MTIME_AT_LOAD for path in _WATCHED_SOURCES)
    return dict(attempts=day.get('attempts',0),loss_latched=day.get('loss_latched',False),
                pnl_vnd=day.get('pnl_vnd'),last_checked=state.get('last_checked'),
                cycle_state=cycle['state'] if cycle else ('UNMANAGED' if unmanaged else 'FLAT'),
                last_error=_LAST_ERROR or state.get('last_error',''),
                snapshot_at=_LAST_SNAPSHOT_AT,unmanaged_positions=unmanaged,
                runtime_pid=os.getpid(),python_version=sys.version.split()[0],
                module_loaded_at=_MODULE_LOADED_AT,restart_required=restart_required,
                read_only_mode=_read_only_mode(),
                side=cycle['side'] if cycle else None,
                sl=cycle['sl'] if cycle else None,tp=cycle.get('tp') if cycle else None,
                overnight=bool(cycle and cycle.get('overnight')),
                overnight_checkpoint_status=cycle.get('overnight_checkpoint_status','') if cycle else '',
                protection=cycle.get('protection') if cycle else None,
                stop_guard_message=state.get('stop_guard_message',''),
                normal_stops=[{'state':g['state'],'qty':g['intent']['qty'],
                               'side':g['intent']['side'],'trigger':g['intent']['trigger']}
                              for g in state.get('normal_stops',[]) if g['state']!='CLOSED'],filled=cycle['entry'].get('filled',0) if cycle else 0)


def ensure_worker():
    global _WORKER, _LAST_ERROR
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return  # never spawn the live Chrome/VPS reconcile thread inside a test process
    from .auto_trader import load_config, cdp_preflight
    cfg=load_config()
    if cfg.get('enabled') and not cfg.get('dry_run',True):
        ok,message=cdp_preflight(cfg)
        if not ok:
            _LAST_ERROR='CDP preflight: '+message
    with _WORKER_LOCK:
        if (_WORKER and _WORKER.is_alive()) or _worker_alive():
            return
        def run():
            from .auto_trader import load_config, _log
            last_error=''
            while True:
                cfg=load_config()
                if not cfg.get('enabled') or cfg.get('dry_run',True):
                    break
                preflight_ok,preflight_message=cdp_preflight(cfg)
                if not preflight_ok:
                    global _LAST_ERROR
                    _LAST_ERROR='CDP preflight: '+preflight_message
                    if _LAST_ERROR!=last_error:
                        _log(_LAST_ERROR)
                    last_error=_LAST_ERROR
                    time.sleep(5); continue
                ok,msg=tick(allow_actions=not _read_only_mode())
                if not ok and msg!=last_error:
                    _log(msg)
                last_error='' if ok else msg
                time.sleep(5)
        _WORKER=threading.Thread(target=run,name=_WORKER_THREAD_NAME,daemon=True)
        _WORKER.start()
