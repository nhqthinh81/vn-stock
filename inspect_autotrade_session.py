"""Read-only SmartPro diagnostics. Never read cookies/PIN or submit orders."""
import json
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from vn_invest.auto_trader import load_config, _session_alive, _ticket_mode, _read_position


def main():
    cfg = load_config()
    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cfg['cdp_url'], timeout=5000)
        try:
            for context in browser.contexts:
                for page in context.pages:
                    url = urlparse(page.url)
                    if url.hostname != 'smartpro.vps.com.vn' or not url.path.startswith('/v1/'):
                        continue
                    alive, message = _session_alive(page)
                    mode, label = _ticket_mode(page)
                    results.append(dict(tab=len(results)+1, dom_session_ok=alive,
                                        session_message=message, mode=mode, mode_label=label,
                                        position_for_configured_symbol=_read_position(page, cfg['symbol_code'])))
        finally:
            browser.close()
    print(json.dumps({'tabs':results, 'server_session_verified':False,
                      'orders_submitted':0}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
