"""Kiểm chứng _place_sltp()/submit_signal(sl_price=...).

Chuyển từ scratchpad/test_sltp.py (phase 28i) sang pytest chính thức.
"""


def test_sltp_placement(at_module):
    at, page = at_module.at, at_module.page
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    cfg = at.load_config()
    cfg["enabled"] = True; cfg["dry_run"] = False
    cfg["auto_all_signals"] = True   # tin hieu thuong cung tu gui, de test don gian
    at.save_config(cfg)

    # 1. sl_price=None -> KHONG goi _place_sltp
    ok, msg = at.submit_signal("LONG", strong=True, price=1981.5, sl_price=None)
    chk(ok, f"mo lenh OK: {msg}")
    chk(len(page.evaluated_sltp) == 0, "khong goi sltp khi sl_price=None")
    chk("SL/TP" not in msg, f"message khong nhac SL/TP: {msg}")

    # 2. sl_price co gia tri -> goi dung 1 lan
    page.clicked.clear(); page.evaluated_sltp.clear()
    ok, msg = at.submit_signal("LONG", strong=True, price=1981.5, sl_price=1965.0, tp_price=None)
    chk(ok, f"mo lenh OK: {msg}")
    chk(len(page.evaluated_sltp) == 1, f"goi sltp dung 1 lan: {len(page.evaluated_sltp)}")
    sym, side, qty, price, sl, tp = page.evaluated_sltp[0]
    chk(side == "B", f"vi the LONG -> vps side='B': {side}")
    chk(sl == "1965.0", f"sl dung: {sl}")
    chk(tp == "0", f"tp_price=None -> gui '0' (khong dat TP): {tp}")
    chk("đã gửi SL/TP" in msg, f"message bao da gui SL/TP: {msg}")

    # 3. Vi the SHORT -> vps side='S'
    page.evaluated_sltp.clear()
    ok, msg = at.submit_signal("SHORT", strong=True, price=1981.5, sl_price=2000.0, tp_price=1940.0)
    chk(ok, f"mo lenh OK: {msg}")
    sym, side, qty, price, sl, tp = page.evaluated_sltp[0]
    chk(side == "S", f"vi the SHORT -> vps side='S': {side}")
    chk(tp == "1940.0", f"tp co gia tri duoc gui dung: {tp}")

    # 4. _place_sltp that bai -> submit_signal VAN True nhung canh bao ro
    def evaluate_sltp_fail(js, arg=None):
        if "co.sltp.order.new" in js:
            return {"sent": False, "err": "gia lap loi mang"}
        return page.__class__.evaluate(page, js, arg)
    page.evaluate = evaluate_sltp_fail
    ok, msg = at.submit_signal("LONG", strong=True, price=1981.5, sl_price=1965.0)
    chk(ok, f"lenh MO van thanh cong du SLTP loi: {msg}")
    chk("KHÔNG đặt được" in msg, f"message canh bao ro SLTP that bai: {msg}")
    del page.evaluate  # tra ve method goc cua class

    # 5. dry_run=True -> KHONG goi _place_sltp
    cfg["dry_run"] = True; at.save_config(cfg)
    page.clicked.clear(); page.evaluated_sltp.clear()
    ok, msg = at.submit_signal("LONG", strong=True, price=1981.5, sl_price=1965.0)
    chk(ok, f"dry-run OK: {msg}")
    chk(len(page.evaluated_sltp) == 0, "dry-run khong goi sltp")

    assert not fails, "\n" + "\n".join(fails)
