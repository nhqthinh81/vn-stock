"""Offline DOM contract tests: separate blank Chrome, no VPS connection or orders."""


def test_order_book_matching_and_position_symbol():
    from playwright.sync_api import sync_playwright
    from vn_invest.auto_trader import _order_snapshot, _read_position
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        try:
            page.set_content('''<table id="order_normal"><tr><th>Header</th></tr>
            <tr><td>1001</td><td>09:00</td><td>TEST</td><td>LONG</td><td>CONTRACT</td><td>1</td><td>0</td><td>1,981.5</td><td></td><td></td><td>Chờ khớp</td></tr>
            <tr><td>1002</td><td>09:00</td><td>OTHER</td><td>LONG</td><td>CONTRACT</td><td>1</td><td>0</td><td>1,981.5</td><td></td><td></td><td>Đã khớp</td></tr></table>
            <table class="tbl-status-danhmuc"><tr><td>OTHER</td><td>-3</td></tr>
            <tr><td>CONTRACT</td><td>+1</td></tr></table>''')
            expected = dict(account='TEST', symbol='CONTRACT', side='LONG', qty=1, price=1981.5)
            rows = _order_snapshot(page, expected)
            assert [r['matches'] for r in rows] == [True, False]
            for key, bad in [('account','MISSING'), ('symbol','MISSING'), ('side','SHORT'), ('qty',2), ('price',1981.6)]:
                assert not any(r['matches'] for r in _order_snapshot(page, {**expected, key:bad}))
            assert _read_position(page, 'CONTRACT') == ('LONG', 1)
            assert _read_position(page, 'MISSING') == ('UNKNOWN', 0)
        finally:
            browser.close()
