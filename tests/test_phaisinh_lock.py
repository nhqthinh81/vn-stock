"""Feedback loop: khoá một-phiên-ghi + mã lệnh toàn cục + chống ghi trùng.

Chuyển từ scratchpad/test_lock.py (phase 25) sang pytest chính thức.
"""
import json

import pandas as pd


def test_owner_lock_and_journal_integrity(ps_module, monkeypatch):
    ps = ps_module
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    # Gia lap 2 phien Streamlit: moi phien la 1 dict session_state rieng
    S = {"A": {}, "B": {}}

    def use(name):
        monkeypatch.setattr(ps.st, "session_state", S[name])

    # 1. Phien A gianh quyen truoc
    use("A"); okA, _ = ps._claim_ownership()
    chk(okA, "A la chu so huu")
    chk(ps._can_trade(), "A duoc phep giao dich")

    # 2. Phien B mo ngay sau -> chi xem
    use("B"); okB, age = ps._claim_ownership()
    chk(not okB, f"B BI TU CHOI (nhip tim A cach {age:.1f}s)")
    chk(not ps._can_trade(), "B khong duoc phep giao dich")

    # 3. B khong the ghi de trang thai cua A
    tok_a = S["A"]["ps_session_token"]
    use("B"); S["B"]["ps_trade_seq"] = 999; ps._save_ps_state()
    own = json.load(open(ps._PS_STATE_FILE, encoding="utf-8"))
    chk(own["owner"]["token"] == tok_a, "chu so huu tren dia van la A")
    chk(own["trade_seq"] != 999, "bo dem cua B khong ghi de duoc")

    # 4. B khong the ghi journal
    use("B")
    ps._append_journal({"time": "2026-08-24 09:20", "ticker": "VN30F1M", "act": "MỞ LONG",
                        "price": 1900.0, "reason": "test-B", "tid": 1})
    chk(not __import__("os").path.exists(ps._JOURNAL_FILE), "journal van trong sau khi B ghi")

    # 5. A ghi journal binh thuong
    use("A")
    for tid, act, px in [(1, "MỞ LONG", 1900.0), (1, "ĐÓNG LONG", 1905.0)]:
        ps._append_journal({"time": f"2026-08-24 09:2{tid}", "ticker": "VN30F1M",
                            "act": act, "price": px, "reason": "test-A", "tid": tid})
    n = len(open(ps._JOURNAL_FILE, encoding="utf-8").read().strip().splitlines())
    chk(n == 3, f"journal co header + 2 dong (thuc te {n})")

    # 6. Chong ghi TRUNG dong cuoi
    before = open(ps._JOURNAL_FILE, encoding="utf-8").read()
    ps._append_journal({"time": "2026-08-24 09:21", "ticker": "VN30F1M",
                        "act": "ĐÓNG LONG", "price": 1905.0, "reason": "test-A", "tid": 1})
    chk(open(ps._JOURNAL_FILE, encoding="utf-8").read() == before,
        "dong trung bi bo qua, file khong doi")

    # 7. Ma lenh cap tu DIA, khong tu RAM
    use("A"); S["A"]["ps_trade_seq"] = 0        # gia lap F5 lam mat bo dem
    chk(ps._next_trade_id() == 2, f"ma tiep theo = 2 (doc max tren dia), duoc {ps._next_trade_id()}")
    use("B"); S["B"]["ps_trade_seq"] = 0        # phien khac cung doc dung con so
    chk(ps._next_trade_id() == 2, "phien B cung thay ma tiep theo = 2 -> khong cap trung")

    # 8. B gianh duoc quyen khi A im lang qua TTL
    d = json.load(open(ps._PS_STATE_FILE, encoding="utf-8"))
    d["owner"]["heartbeat"] = (pd.Timestamp.now()
                               - pd.Timedelta(seconds=ps._OWNER_TTL_SEC + 5)).isoformat()
    json.dump(d, open(ps._PS_STATE_FILE, "w", encoding="utf-8"))
    use("B"); okB2, _ = ps._claim_ownership()
    chk(okB2, "B gianh duoc quyen sau khi A het han")
    chk(json.load(open(ps._PS_STATE_FILE, encoding="utf-8"))["owner"]["token"]
        == S["B"]["ps_session_token"], "chu so huu tren dia da la B")

    assert not fails, "\n" + "\n".join(fails)


def test_trading_minutes_excludes_lunch_break():
    from vn_invest.daily_report import _trading_minutes as tm
    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    T = pd.Timestamp
    cases = [(T("2026-08-20 09:20"), T("2026-08-20 09:50"), 30, "trong phien 1"),
             (T("2026-08-20 13:05"), T("2026-08-20 13:35"), 30, "trong phien 2"),
             (T("2026-08-20 11:29"), T("2026-08-20 13:29"), 30, "bac qua nghi trua"),
             (T("2026-08-20 11:20"), T("2026-08-20 13:00"), 10, "vao truoc nghi, ra dau phien 2")]
    for a, b, want, lbl in cases:
        got = tm(a, b)
        chk(got == want, f"{lbl}: {a:%H:%M}->{b:%H:%M} = {got} phut (mong {want})")

    assert not fails, "\n" + "\n".join(fails)
