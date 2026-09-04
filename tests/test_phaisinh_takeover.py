"""Kịch bản thật của user: 2 cửa sổ, cửa sổ chính chết khi ĐANG GIỮ LỆNH.

Cửa sổ dự phòng phải tiếp quản VÀ thấy lệnh đang mở — nếu không sẽ mở thêm
lệnh thứ hai, còn lệnh cũ kẹt với dòng MỞ không bao giờ có ĐÓNG.

Chuyển từ scratchpad/test_takeover.py (phase 26) sang pytest chính thức.
"""
import json

import pandas as pd


def test_hot_standby_takes_over_open_position(ps_module, monkeypatch):
    ps = ps_module
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    S = {"A": {}, "B": {}}

    def use(name):
        monkeypatch.setattr(ps.st, "session_state", S[name])

    def expire_owner():
        d = json.load(open(ps._PS_STATE_FILE, encoding="utf-8"))
        d["owner"]["heartbeat"] = (pd.Timestamp.now()
                                   - pd.Timedelta(seconds=ps._OWNER_TTL_SEC + 5)).isoformat()
        json.dump(d, open(ps._PS_STATE_FILE, "w", encoding="utf-8"))

    # 1. Cua so A gianh quyen va MO LENH
    use("A"); ps._claim_ownership()
    ts = pd.Timestamp("2026-08-25 09:20")
    pos = ps._open_position("LONG", 1890.0, ts, atr=2.0, tid=ps._next_trade_id(), tp_r=3.0)
    S["A"]["ps_position"] = pos
    ps._save_ps_state()
    ps._append_journal({"time": "2026-08-25 09:20", "ticker": "VN30F1M", "act": "MỞ LONG",
                        "price": 1890.0, "sl": pos["sl"], "reason": "test", "tid": pos["tid"]})
    chk(pos["tid"] == 1, f"A mo lenh #{pos['tid']} LONG @1890")

    # 2. Cua so B mo len — phai la DU PHONG, khong thay doi gi
    use("B"); okB, _ = ps._claim_ownership()
    chk(not okB, "B o che do chi xem")
    chk(S["B"].get("ps_position") is None, "B chua biet lenh cua A (dung — chua tiep quan)")

    # 3. Cua so A CHET (nhip tim het han)
    expire_owner()

    # 4. B tiep quan — PHAI thay lenh dang mo cua A
    use("B"); okB2, _ = ps._claim_ownership()
    chk(okB2, "B da tiep quan quyen dieu khien")
    p = S["B"].get("ps_position")
    chk(p is not None, "B THAY duoc lenh dang mo (khong bo roi lenh)")
    if p:
        chk(p["tid"] == 1 and p["side"] == "LONG" and abs(p["entry"] - 1890.0) < 1e-9,
            f"dung lenh: #{p['tid']} {p['side']} @{p['entry']}")
        chk(abs(p["sl"] - pos["sl"]) < 1e-9, f"giu nguyen SL {p['sl']}")
    chk(S["B"].get("ps_took_over_at") is not None, "co danh dau thoi diem tiep quan (de bao UI)")

    # 5. B khong cap trung ma lenh
    chk(ps._next_trade_id() == 2, f"ma tiep theo = 2, duoc {ps._next_trade_id()}")

    # 6. B dong lenh — journal ghep cap dung
    ps._append_journal({"time": "2026-08-25 09:35", "ticker": "VN30F1M", "act": "ĐÓNG LONG",
                        "price": 1895.0, "sl": p["sl"], "pnl": "+5.0", "reason": "test",
                        "tid": p["tid"], "result": "THẮNG"})
    from vn_invest.daily_report import load_trades
    tr = load_trades(journal_file=ps._JOURNAL_FILE)
    chk(len(tr) == 1, f"ghep duoc dung 1 lenh (duoc {len(tr)})")
    if len(tr):
        r = tr.iloc[0]
        chk(abs(r.entry - 1890.0) < 1e-9 and abs(r.exit - 1895.0) < 1e-9,
            f"vao {r.entry} -> ra {r.exit} (khong ghep nham)")

    assert not fails, "\n" + "\n".join(fails)
