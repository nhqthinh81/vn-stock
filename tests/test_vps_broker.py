"""Broker adapter contract tests on a fully intercepted browser, never VPS."""
import hashlib
import json
from pathlib import Path
import pytest
from vn_invest.vps_broker import Broker, order_state, number, _READ_JS, _SEND_JS


@pytest.mark.parametrize('qty,filled,code,raw,expected',[
    (2,2,'7','PM','FILLED'),(2,1,'6','PM','PARTIAL'),(2,1,'8','PMX','CANCELED'),
    (2,0,'1','P','PENDING'),(2,0,'','PR','REJECTED'),(2,0,'???','???','UNKNOWN'),
])
def test_order_states(qty,filled,code,raw,expected):
    assert order_state(dict(qty=qty,filled=filled,status_code=code,status=raw))==expected


@pytest.mark.parametrize('value',[None,'',True,'NaN','inf'])
def test_missing_or_nonfinite_broker_numbers_fail_closed(value):
    with pytest.raises(ValueError):number(value,'pnl')


@pytest.fixture(scope='module')
def offline_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(channel='chrome',headless=True)
        yield b
        b.close()


@pytest.fixture
def wire(offline_browser):
    context=offline_browser.new_context()
    page=context.new_page()
    calls=[]
    replies={}
    acct='TEST8'
    position=dict(account=acct,symbol='41I1G9000',net=0,lastPrice=1981.5,avg_remain=0,duedate='17/09/2026')
    replies.update({
        'Web.Portfolio.AccountStatus':{'rc':1,'data':dict(acc_code=acct,vm=-100000.,unrelizeVM=0.)},
        'Web.Portfolio.PortfolioStatus2':{'rc':1,'data':[position]},
        'Web.Order.FullAllOrder':{'rc':1,'data':[]},
        'list_condition_order':{'rc':1,'data':[]},
    })
    def route_handler(route):
        request=route.request
        if request.method=='GET':
            route.fulfill(status=200,content_type='text/html',body='<select id="right_account"><option>TEST8</option></select>')
            return
        body=request.post_data_json
        calls.append(body)
        cmd=body['data']['cmd']
        response=replies.get(cmd,{'rc':1,'data':{'private':'must-not-escape'}})
        route.fulfill(status=200,content_type='application/json',body=json.dumps(response))
    context.route('**/*',route_handler)
    page.goto('https://smartpro.vps.com.vn/v1/')
    page.evaluate('''() => {
        window.global={user:'TEST',sid:'TEST-SESSION'};
        window.urlCore='/query';window.urlCoreExt='/orders';
        window.utils={fnGetDeviceInfo:()=> 'TEST-DEVICE'};window.getGssc=()=> 'TEST-GSSC';
        window.orderNormalParseData=(account,pin,price,side,volume,symbol,refId,orderType,group)=>
            ({group,data:{cmd:'Web.newOrder',account,pin,price,side,volume,symbol,refId,orderType}});
    }''')
    yield page,calls,replies
    context.close()


def test_snapshot_queries_only_whitelist_and_keeps_credentials_out(wire):
    page,calls,_=wire
    s=Broker(page,{}).snapshot()
    assert s['pnl_vnd']==-100000.
    assert s['account_ref']==hashlib.sha256(b'TEST8').hexdigest()
    assert 'TEST-SESSION' not in json.dumps(s) and 'TEST8' not in json.dumps(s)
    assert {c['data']['cmd'] for c in calls}=={
        'Web.Portfolio.AccountStatus','Web.Portfolio.PortfolioStatus2','Web.Order.FullAllOrder','list_condition_order'}


def test_snapshot_rejects_expired_server_session_even_with_ticket(wire):
    page,calls,replies=wire
    replies['Web.Portfolio.AccountStatus']={'rc':-1,'rs':'FOException.InvalidSessionException'}
    with pytest.raises(Exception,match='VPS_READ_REJECTED'):Broker(page,{}).snapshot()


def test_snapshot_rejects_wrong_account_response(wire):
    page,calls,replies=wire
    replies['Web.Portfolio.AccountStatus']['data']['acc_code']='OTHER'
    with pytest.raises(Exception,match='ACCOUNT_RESPONSE_MISMATCH'):Broker(page,{}).snapshot()


def test_read_helper_cannot_execute_trade(wire):
    page,calls,_=wire
    with pytest.raises(Exception,match='READ_COMMAND_NOT_ALLOWED'):
        page.evaluate(_READ_JS,dict(command='Web.newOrder',pageNo=1,fromDate='',toDate=''))
    assert not calls


def test_single_entry_request_is_parent_with_sl_tp(wire):
    page,calls,_=wire
    intent=dict(id='id-1',symbol='41I1G9000',side='LONG',price=1981.5,qty=1,sl=1975.,tp=None)
    result=Broker(page,{}).send('entry',intent,hashlib.sha256(b'TEST8').hexdigest())
    assert result=={'outcome':'ACK'}
    assert len(calls)==1
    data=calls[0]['data']
    assert data['cmd']=='co.sltp.order.new'
    assert data['stopLossPrice']=='1975' or data['stopLossPrice']=='1975.0'
    assert data['takeProfitPrice']=='0'
    assert data['side']=='B'
    assert 'private' not in json.dumps(result)


def test_exit_uses_native_checksum_builder_and_stable_reference(wire):
    page,calls,_=wire
    intent=dict(id='intent-123',symbol='41I1G9000',side='SHORT',qty=1)
    Broker(page,{}).send('exit',intent,hashlib.sha256(b'TEST8').hexdigest())
    data=calls[0]['data']
    assert data['cmd']=='Web.newOrder' and data['price']=='MTL'
    assert data['refId']=='TEST.H.intent-123' and data['side']=='S'


def test_send_account_change_returns_unknown_without_network(wire):
    page,calls,_=wire
    r=Broker(page,{}).send('exit',dict(id='i',symbol='S',side='LONG',qty=1),'different-account-hash')
    assert r['outcome']=='UNKNOWN' and not calls


def test_http_ok_with_rc_zero_is_rejection(wire):
    page,calls,replies=wire
    replies['co.sltp.order.new']={'rc':0,'data':{'code':'FOS-6012'}}
    r=Broker(page,{}).send('entry',dict(id='i',symbol='S',side='LONG',qty=1,price=100,sl=99,tp=None),
                           hashlib.sha256(b'TEST8').hexdigest())
    assert r=={'outcome':'REJECTED'}


@pytest.mark.parametrize('side,sl,tp,expected',[('LONG',1975.,1995.,'B'),('SHORT',1995.,1975.,'S')])
def test_parent_contains_both_thresholds_in_one_post(wire,side,sl,tp,expected):
    page,calls,_=wire
    result=Broker(page,{}).send('entry',dict(id='pair',symbol='41I1G9000',side=side,
        price=1981.5,qty=2,sl=sl,tp=tp),hashlib.sha256(b'TEST8').hexdigest())
    assert result['outcome']=='ACK' and len(calls)==1
    data=calls[0]['data']
    assert data['cmd']=='co.sltp.order.new' and data['side']==expected
    assert float(data['stopLossPrice'])==sl and float(data['takeProfitPrice'])==tp
    assert data['quantity']=='2' and data['triggerType']=='price'


@pytest.mark.parametrize('side,relation',[('SHORT','LTEQ'),('LONG','GTEQ')])
def test_protective_stop_payload_is_conditional_opposite_order(wire,side,relation):
    page,calls,_=wire
    r=Broker(page,{}).send('protect_stop',dict(side=side,relation=relation,qty=1,trigger=1980.,symbol='S'),
                          hashlib.sha256(b'TEST8').hexdigest())
    assert r['outcome']=='ACK' and len(calls)==1
    data=calls[0]['data']
    assert data['cmd']=='co.stop.order.new' and data['priceType']=='MTL'
    assert data['relation']==relation and 'placedPrice' not in data
    assert 'stopLossPrice' not in data


def test_stop_cancel_uses_native_command(wire):
    page,calls,_=wire
    Broker(page,{}).send('cancel_condition',dict(type='stop',target='stop-id'),hashlib.sha256(b'TEST8').hexdigest())
    assert calls[0]['data']==dict(cmd='co.stop.order.delete',pin='',orderId='stop-id')


def test_invalid_stop_relation_never_posts(wire):
    page,calls,_=wire
    r=Broker(page,{}).send('protect_stop',dict(side='SHORT',relation='GTEQ',qty=1,trigger=100.,symbol='S'),
                          hashlib.sha256(b'TEST8').hexdigest())
    assert r['outcome']=='UNKNOWN' and not calls
