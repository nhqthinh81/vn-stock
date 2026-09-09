"""SmartPro adapter. Credentials remain inside the authenticated browser page.

Query schemas and native orderNormalParseData were checked against SmartPro on
2026-09-07. No raw responses, account numbers, cookies or PINs are persisted.
"""
from __future__ import annotations
import hashlib
import math
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))


def now_vn():
    return datetime.now(VN)


def number(value, name):
    if value is None or isinstance(value, bool) or str(value).strip() == '':
        raise ValueError(f'Thiếu {name} từ VPS')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'{name} không hợp lệ từ VPS')
    return result


# Only these read commands can pass through this helper.
_READ_JS = r'''async ({command, pageNo, fromDate, toDate}) => {
    const account = document.querySelector('#right_account')?.value;
    if (!account || typeof global === 'undefined' || !global.sid || !global.user)
        throw new Error('VPS_SESSION_MISSING');
    // The native portfolio screen is bound to user + 8. Do not silently read
    // that account while the order ticket points at a different one.
    if (account !== global.user + '8') throw new Error('VPS_ACCOUNT_MISMATCH');
    const allowed = ['Web.Portfolio.AccountStatus','Web.Portfolio.PortfolioStatus2',
                     'Web.Order.FullAllOrder','list_condition_order'];
    if (!allowed.includes(command)) throw new Error('READ_COMMAND_NOT_ALLOWED');
    let data = {type:'string',cmd:command,p1:account,p2:'',p3:'',p4:'null'};
    let group = 'Q';
    if (command === 'Web.Order.FullAllOrder')
        data = {type:'string',cmd:command,p1:String(pageNo),p2:'200',p3:account+',ALL,ALL',p4:''};
    if (command === 'list_condition_order') {
        group = 'B';
        data = {type:'cursor',cmd:command,p1:account,p2:'',p3:fromDate,p4:toDate,
                p5:'',p6:'',p7:'',p8:String(pageNo),p9:'200'};
    }
    const target = new URL(urlCore, location.href);
    if (target.origin !== location.origin) throw new Error('VPS_ORIGIN_MISMATCH');
    const r = await fetch(target.href, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json; charset=utf-8'},
        signal:AbortSignal.timeout(8000),
        body:JSON.stringify({group,user:global.user,session:global.sid,c:'H',data})});
    if (!r.ok) throw new Error('VPS_READ_HTTP_ERROR');
    const j = await r.json();
    if (Number(j.rc) !== 1) throw new Error('VPS_READ_REJECTED');
    if (document.querySelector('#right_account')?.value !== account) throw new Error('VPS_ACCOUNT_CHANGED');
    if (command === 'Web.Portfolio.AccountStatus') {
        if (!j.data || j.data.acc_code !== account) throw new Error('VPS_ACCOUNT_RESPONSE_MISMATCH');
        return {account, data:{vm:j.data.vm, unrelizeVM:j.data.unrelizeVM}};
    }
    if (!Array.isArray(j.data)) throw new Error('VPS_READ_SCHEMA_CHANGED');
    if (command === 'Web.Portfolio.PortfolioStatus2')
        return {account,data:j.data.map(x=>({account_ok:x.account===account,symbol:x.symbol,
            net:x.net,avg:x.avg_remain,last:x.lastPrice,due:x.duedate}))};
    if (command === 'Web.Order.FullAllOrder')
        return {account,data:j.data.filter(x=>x.accountCode===account).map(x=>({
            id:String(x.pk_orderNo),number:String(x.orderNo),side:x.side,symbol:x.symbol,
            qty:x.volume,filled:x.matchVolume,avg:x.avgPrice,price:x.showPrice,
            status_code:String(x.status_code),status:x.status,time:x.orderTime})),raw_count:j.data.length};
    return {account,data:j.data.filter(x=>x.ACCOUNT_NO===account).map(x=>({
        id:String(x.STOP_ORDER_ID),number:String(x.PK_ID),symbol:x.SYMBOL,side:x.SIDE,
        type:x.STOP_ORDER_TYPE,subtype:x.STOP_ORDER_SUB_TYPE,status:x.STATUS,
        order_status:x.ORDER_STATUS,qty:x.QUANTITY,remaining:x.REMAIN_QTY,
        trigger:x.TRIGGER_PRICE,relation:x.RELATION,price_type:x.PRICE_TYPE,parent:String(x.FO_PK_ORI_ORDER_ID),parent_number:String(x.ORI_ORDER_ID),
        child:String(x.FO_PK_ORDER_ID),child_number:String(x.FO_ORDER_ID)})),raw_count:j.data.length};
}'''


# Mutating code is called by the runtime only AFTER an intent is durably saved.
# The assistant's verification never calls this against the real account.
_SEND_JS = r'''async ({kind, intent, accountRef}) => {
    const account = document.querySelector('#right_account')?.value;
    if (!account || account !== global.user+'8') throw new Error('VPS_ACCOUNT_MISMATCH');
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(account));
    const ref = [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
    if (ref !== accountRef) throw new Error('VPS_ACCOUNT_CHANGED');
    const side = intent.side === 'LONG' ? 'B' : 'S';
    let payload, endpoint;
    if (kind === 'entry') {
        // A single opening order WITH attached protection. This command must
        // never be sent after a separate opening order (it would open twice).
        payload = {group:'O',user:global.user,session:global.sid,
            extInfo:utils.fnGetDeviceInfo(),language:'vi',data:{
                cmd:'co.sltp.order.new',accountNo:account,pin:'',channel:'H',
                placedPrice:String(intent.price),priceType:'LO',side,quantity:String(intent.qty),
                stopOrderType:'sl_tp',symbol:intent.symbol,spread:'',triggerType:'price',
                stopLossPrice:String(intent.sl),takeProfitPrice:String(intent.tp ?? 0),
                stopLossAmount:'0',takeProfitAmount:'0'}};
        endpoint = urlCoreExt;
    } else if (kind === 'protect_stop') {
        if (!Number.isInteger(intent.qty) || intent.qty <= 0 ||
            !Number.isFinite(intent.trigger) || intent.trigger <= 0 ||
            !['LONG','SHORT'].includes(intent.side) ||
            intent.relation !== (side === 'S' ? 'LTEQ' : 'GTEQ'))
            throw new Error('INVALID_PROTECTIVE_STOP');
        payload = {group:'O',user:global.user,session:global.sid,
            extInfo:utils.fnGetDeviceInfo(),language:'vi',data:{
                cmd:'co.stop.order.new',accountNo:account,pin:'',channel:'H',
                priceType:'MTL',quantity:String(intent.qty),relation:intent.relation,
                side,stopOrderType:'stop',symbol:intent.symbol,triggerPrice:String(intent.trigger)}};
        endpoint = urlCoreExt;
    } else if (kind === 'exit') {
        payload = orderNormalParseData(account,'','MTL',side,intent.qty,intent.symbol,
                                      global.user+'.H.'+intent.id,'1','FD');
        endpoint = urlCore;
    } else if (kind === 'cancel_order') {
        payload = {group:'FD',user:global.user,session:global.sid,extInfo:utils.fnGetDeviceInfo(),c:'H',
            data:{type:'string',cmd:'Web.cancelOrder',orderNo:intent.target,
                  refId:global.user+'.H.'+intent.id,fisID:'',orderType:'1',pin:''}};
        endpoint = urlCore;
    } else if (kind === 'cancel_condition') {
        if (!['sl_tp','stop'].includes(intent.type)) throw new Error('UNMANAGED_CONDITION_TYPE');
        payload = {group:'O',user:global.user,session:global.sid,extInfo:utils.fnGetDeviceInfo(),
            data: intent.type === 'stop'
                ? {cmd:'co.stop.order.delete',pin:'',orderId:intent.target}
                : {cmd:'co.sltp.order.delete',accountNo:account,pin:'',orderId:intent.target}};
        endpoint = urlCoreExt;
    } else throw new Error('UNKNOWN_TRADE_ACTION');
    const target = new URL(endpoint,location.href);
    if (target.origin !== location.origin) throw new Error('VPS_ORIGIN_MISMATCH');
    const r = await fetch(target.href,{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json; charset=utf-8','Accept-Language':'vi','X-Device-New':getGssc()},
        signal:AbortSignal.timeout(8000),body:JSON.stringify(payload)});
    if (!r.ok) return {outcome:'UNKNOWN'};
    const j = await r.json();
    // Do not pass raw server errors or credential-bearing request data to logs.
    // null/false/blank must not become zero and masquerade as a rejection.
    const raw = j?.rc;
    if (!(typeof raw === 'number' || (typeof raw === 'string' && /^-?\d+$/.test(raw.trim()))))
        return {outcome:'UNKNOWN'};
    const rc = Number(raw);
    if (!Number.isSafeInteger(rc)) return {outcome:'UNKNOWN'};
    if (rc <= 0) return {outcome:'REJECTED',response_rc:rc};
    if (rc !== 1) return {outcome:'UNKNOWN',response_rc:rc};
    return {outcome:'ACK'};
}'''


def order_state(row):
    qty, filled = number(row['qty'], 'KL đặt'), number(row['filled'], 'KL khớp')
    if qty <= 0 or filled < 0 or filled > qty or not qty.is_integer() or not filled.is_integer():
        raise ValueError('Khối lượng sổ lệnh VPS không hợp lệ')
    code = str(row['status_code'])
    if filled == qty:
        return 'FILLED'
    if code in ('4', '8', '9') or 'X' in row['status']:
        return 'CANCELED'
    if 'R' in row['status']:
        return 'REJECTED'
    if code in ('0','1','2','3','5','6'):
        return 'PARTIAL' if filled else 'PENDING'
    return 'UNKNOWN'


class Broker:
    def __init__(self, page, cfg):
        self.page, self.cfg = page, cfg

    def _read(self, command, page_no=1, since=None):
        today = now_vn().date()
        raw = self.page.evaluate(_READ_JS, dict(command=command,pageNo=page_no,
            fromDate=(since or today).strftime('%d/%m/%Y'),toDate=today.strftime('%d/%m/%Y')))
        account = raw.pop('account')
        raw['account_ref'] = hashlib.sha256(account.encode()).hexdigest()
        return raw

    def snapshot(self, since=None):
        started = now_vn()
        account = self._read('Web.Portfolio.AccountStatus')
        pos = self._read('Web.Portfolio.PortfolioStatus2')
        ref = account['account_ref']
        if pos['account_ref'] != ref or any(not x.pop('account_ok') for x in pos['data']):
            raise ValueError('Tài khoản VPS thay đổi khi đọc vị thế')
        orders, conditions = [], []
        for command, target in [('Web.Order.FullAllOrder',orders),('list_condition_order',conditions)]:
            for page_no in range(1, 26):
                r = self._read(command,page_no,since)
                if r['account_ref'] != ref:
                    raise ValueError('Tài khoản VPS thay đổi khi đọc sổ lệnh')
                target.extend(r['data'])
                if r['raw_count'] < 200:
                    break
            else:
                raise ValueError('Sổ lệnh vượt giới hạn đối soát — chưa đọc đủ')
        for x in pos['data']:
            x['net'] = number(x['net'],'vị thế')
            if not x['net'].is_integer():
                raise ValueError('Vị thế VPS không nguyên')
            x['net'] = int(x['net'])
            x['last'] = number(x['last'],'giá')
            x['avg'] = number(x['avg'],'giá vốn') if x['net'] else 0
        for x in orders:
            x['state'] = order_state(x)
            x['qty'], x['filled'] = int(float(x['qty'])), int(float(x['filled']))
            x['avg'] = number(x['avg'],'giá khớp') if x['filled'] else 0
        checked = now_vn()
        if checked.date() != started.date():
            raise ValueError('Ngày giao dịch thay đổi trong lúc đọc VPS; cần đọc lại')
        return dict(account_ref=ref,pnl_vnd=number(account['data']['vm'],'lãi/lỗ VPS'),
                    positions=pos['data'],orders=orders,conditions=conditions,checked_at=checked.isoformat())

    def send(self, kind, intent, account_ref):
        try:
            return self.page.evaluate(_SEND_JS, dict(kind=kind,intent=intent,accountRef=account_ref))
        except Exception:
            return {'outcome':'UNKNOWN'}


@contextmanager
def connect(cfg):
    from . import auto_trader as at
    from playwright.sync_api import sync_playwright
    at._ensure_win_proactor_policy()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cfg['cdp_url'],timeout=5000)
        try:
            page = at._find_vps_page(browser,cfg['page_url_contains'])
            if page is None:
                raise ValueError('Không thấy tab SmartPro')
            alive, message = at._session_alive(page)
            if not alive:
                raise ValueError(message)
            yield Broker(page,cfg)
        finally:
            browser.close()
