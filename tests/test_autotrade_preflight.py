from vn_invest import auto_trader as at


def test_cdp_preflight_reports_stale_port_without_switching(monkeypatch):
    calls=[]
    def read(url,timeout=3.0):
        calls.append(url)
        if url.endswith(':9333'):
            raise ConnectionRefusedError
        return [{'type':'page','url':'https://smartpro.vps.com.vn/v1/'}]
    monkeypatch.setattr(at,'_read_cdp_pages',read)
    ok,message=at.cdp_preflight({'cdp_url':'http://127.0.0.1:9333'})
    assert not ok and 'SmartPro đang ở http://127.0.0.1:9222' in message
    assert calls==['http://127.0.0.1:9333','http://127.0.0.1:9222']


def test_cdp_preflight_requires_exactly_one_smartpro_tab(monkeypatch):
    monkeypatch.setattr(at,'_read_cdp_pages',lambda *args,**kwargs:[
        {'type':'page','url':'https://smartpro.vps.com.vn/v1/'},
        {'type':'page','url':'https://smartpro.vps.com.vn/v1/'},
    ])
    ok,message=at.cdp_preflight({'cdp_url':'http://127.0.0.1:9333'})
    assert not ok and 'đúng một tab SmartPro' in message


def test_cdp_preflight_ignores_non_page_targets(monkeypatch):
    monkeypatch.setattr(at,'_read_cdp_pages',lambda *args,**kwargs:[
        {'type':'page','url':'https://smartpro.vps.com.vn/v1/'},
        {'type':'service_worker','url':'https://smartpro.vps.com.vn/v1/'},
    ])
    ok,message=at.cdp_preflight({'cdp_url':'http://127.0.0.1:9333'})
    assert ok
    assert 'CDP sẵn sàng' in message
