"""Dò phiếu lệnh SmartPro để điền selector cho auto_trader.

Cách dùng:
  1. Chạy Chay_Chrome_AutoTrade.bat, đăng nhập SmartPro, mở màn hình đặt lệnh
     phái sinh (thấy được ô mã HĐ / giá / khối lượng / nút MUA BÁN).
  2. python inspect_vps.py
  3. Gửi file data/vps_dom_dump.json cho Claude để điền autotrade_config.json.

Chỉ ĐỌC trang — không bấm, không điền gì.
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vn_invest.auto_trader import load_config, _find_vps_page  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "data", "vps_dom_dump.json")

_JS = """
() => {
  const pick = (el) => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    name: el.getAttribute('name'),
    cls: (el.className && String(el.className).slice(0, 120)) || null,
    placeholder: el.getAttribute('placeholder'),
    type: el.getAttribute('type'),
    text: (el.innerText || el.value || '').trim().slice(0, 60) || null,
    testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
    visible: !!(el.offsetWidth || el.offsetHeight),
  });
  return {
    url: location.href,
    title: document.title,
    inputs: [...document.querySelectorAll('input, textarea')].map(pick),
    buttons: [...document.querySelectorAll(
      'button, [role="button"], a.btn, div.btn, span.btn')].map(pick).filter(b => b.text),
  };
}
"""


def main() -> int:
    cfg = load_config()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Thiếu playwright — pip install playwright")
        return 1
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cfg["cdp_url"], timeout=5000)
        except Exception as e:
            print(f"Không nối được {cfg['cdp_url']}: {e}")
            print("→ Chrome phải đang chạy bằng Chay_Chrome_AutoTrade.bat")
            return 1
        page = _find_vps_page(browser, cfg["page_url_contains"])
        if page is None:
            print(f"Không thấy tab chứa '{cfg['page_url_contains']}'. Các tab đang mở:")
            for c in browser.contexts:
                for pg in c.pages:
                    print("  -", pg.url[:100])
            browser.close()
            return 1

        dump = page.evaluate(_JS)
        # thêm dump của các iframe (phiếu lệnh hay nằm trong iframe)
        dump["frames"] = []
        for fr in page.frames[1:]:
            try:
                d = fr.evaluate(_JS)
                d["frame_url"] = fr.url
                dump["frames"].append(d)
            except Exception:
                pass
        browser.close()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    data = json.dumps(dump, ensure_ascii=False, indent=1).encode("utf-8")
    tmp = OUT + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, OUT)

    vis_in  = [i for i in dump["inputs"] if i["visible"]]
    vis_btn = [b for b in dump["buttons"] if b["visible"]]
    print(f"Trang: {dump['title']}  ({dump['url'][:70]})")
    print(f"Thấy {len(vis_in)} ô nhập + {len(vis_btn)} nút hiện hữu "
          f"(+{len(dump['frames'])} iframe)")
    print("\nCác nút đáng chú ý:")
    for b in vis_btn:
        t = (b["text"] or "").upper()
        if any(k in t for k in ("MUA", "BÁN", "BAN", "LONG", "SHORT", "ĐẶT", "DAT",
                                "XÁC NHẬN", "XAC NHAN", "BUY", "SELL")):
            print(f"  [{b['text']}]  id={b['id']} cls={str(b['cls'])[:60]}")
    print(f"\n→ Đã lưu {OUT}")
    print("→ Gửi file này cho Claude để điền selector vào autotrade_config.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
