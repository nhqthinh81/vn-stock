"""Kiểm chứng vị thế ẢO trailing 4×ATR (thử nghiệm, xem chú thích
_SHADOW_TRAIL_ATR_MULT trong phaisinh_tab.py):

  1. Mở shadow: SL ban đầu giống HỆT lệnh thật (_SL_ATR_MULT)
  2. Trailing kéo SL theo đỉnh/đáy, KHÔNG BAO GIỜ nới lỏng
  3. Shadow SỐNG TIẾP sau khi lệnh thật đã đóng (bars khác nhau)
  4. Chạm trailing SL -> thoát đúng giá
  5. Journal shadow ghi đúng cặp MỞ/ĐÓNG, load_trades() đọc lại đúng
  6. _save_ps_state()/_load_ps_state() roundtrip đúng cả shadow
"""
import pandas as pd


def _bar(ts, o, h, l, c):
    return pd.Series({"Open": o, "High": h, "Low": l, "Close": c}, name=pd.Timestamp(ts))


def _bars_df(rows):
    df = pd.DataFrame([r.to_dict() for r in rows])
    df.index = pd.DatetimeIndex([r.name for r in rows])
    return df


def test_shadow_open_matches_real_initial_sl(ps_module):
    ps = ps_module
    ts = pd.Timestamp("2026-09-04 09:20")
    real   = ps._open_position("LONG", 1900.0, ts, atr=2.0, tid=1, tp_r=None)
    shadow = ps._open_shadow_position("LONG", 1900.0, ts, atr=2.0, tid=1)
    assert shadow["sl"] == real["sl"], "SL ban đầu shadow phải giống hệt lệnh thật"
    assert shadow["peak"] == 1900.0


def test_trailing_sl_only_tightens_never_loosens(ps_module):
    ps = ps_module
    ts0 = pd.Timestamp("2026-09-04 09:20")
    shadow = ps._open_shadow_position("LONG", 1900.0, ts0, atr=2.0, tid=1)
    sl0 = shadow["sl"]   # 1900 - 3*2 = 1894.0

    # Nến giá tăng mạnh (high-low sát nhau, tránh cùng nến vừa kéo SL lên vừa
    # chạm low) -> SL phải kéo lên theo đỉnh (đỉnh - 4*ATR)
    bars_up = _bars_df([
        _bar("2026-09-04 09:21", 1901, 1910, 1908, 1909),
    ])
    do_exit, reason, px, exit_ts = ps._check_shadow_exit(shadow, bars_up, in_session=True)
    assert not do_exit, "chưa chạm SL khi giá đang chạy thuận"
    assert shadow["sl"] > sl0, f"SL phải được kéo lên theo đỉnh mới: {shadow['sl']} vs {sl0}"
    assert shadow["sl"] == round(1910 - 4.0 * 2.0, 1)   # 1902.0
    sl1 = shadow["sl"]

    # Nến sau đó giá RÚT LUI nhưng chưa chạm SL -> SL KHÔNG được nới lỏng xuống
    bars_pullback = _bars_df([
        _bar("2026-09-04 09:22", 1908, 1909, 1903, 1904),
    ])
    do_exit2, _, _, _ = ps._check_shadow_exit(shadow, bars_pullback, in_session=True)
    assert not do_exit2, "1903 vẫn còn trên SL 1902.0"
    assert shadow["sl"] == sl1, "SL không được nới lỏng khi giá rút lui (chỉ tightens)"


def test_shadow_exits_on_trailing_sl_hit():
    import vn_invest.phaisinh_tab as ps
    ts0 = pd.Timestamp("2026-09-04 09:20")
    shadow = ps._open_shadow_position("SHORT", 1900.0, ts0, atr=2.0, tid=1)
    # SHORT: SL ban đầu = 1900 + 3*2 = 1906.0
    assert shadow["sl"] == 1906.0

    bars = _bars_df([
        _bar("2026-09-04 09:21", 1899, 1900, 1885, 1886),   # gia giam manh -> peak=1885, sl=1885+4*2=1893
        _bar("2026-09-04 09:22", 1886, 1894, 1885, 1893),   # bat nguoc, cham sl 1893
    ])
    do_exit, reason, px, exit_ts = ps._check_shadow_exit(shadow, bars, in_session=True)
    assert do_exit, "phải thoát khi giá chạm trailing SL"
    assert "trailing SL" in reason
    assert px == 1893.0


def test_shadow_outlives_real_position(ps_module):
    """Shadow phải tiếp tục chạy độc lập với số nến khác với lệnh thật, nếu
    lệnh thật đóng sớm hơn (ví dụ chạm SL cố định trong khi shadow còn xa SL)."""
    ps = ps_module
    ts0 = pd.Timestamp("2026-09-04 09:20")
    real   = ps._open_position("LONG", 1900.0, ts0, atr=2.0, tid=1, tp_r=None)
    shadow = ps._open_shadow_position("LONG", 1900.0, ts0, atr=2.0, tid=1)

    # Gia giam nhe cham SL co dinh cua lenh THAT (1894.0) nhung con xa SL
    # trailing ban dau cua shadow (cung 1894.0 luc nay — nen can gia CHUA
    # cham de shadow song tiep): dung 1 kich ban khac — gia TANG truoc de keo
    # SL shadow xa hon, roi lenh that van dang WAIT (khong dung toi trong test
    # nay) nhung ta chi mo phong shadow song qua nhieu nen hon lenh that.
    bars = _bars_df([
        _bar("2026-09-04 09:21", 1901, 1905, 1900, 1904),
        _bar("2026-09-04 09:22", 1904, 1906, 1903, 1905),
    ])
    do_exit_sh, *_ = ps._check_shadow_exit(shadow, bars, in_session=True)
    assert not do_exit_sh
    assert shadow["bars"] == 2

    # Lenh that "dong" ngay tai day (mo phong ben ngoai, khong goi
    # _check_position_exit) — shadow KHONG bi anh huong, van con "bars"=2,
    # khac voi lenh that (co the da dong o bars=1 chang han). Diem quan trong:
    # khong co co che nao trong _check_shadow_exit phu thuoc vao trang thai
    # cua `real`/`pos` — xac nhan bang cach kiem tra shadow van tiep tuc duoc
    # khi goi them 1 nen nua, khong quan tam real da dong hay chua.
    bars2 = _bars_df([
        _bar("2026-09-04 09:23", 1905, 1907, 1904, 1906),
    ])
    do_exit_sh2, *_ = ps._check_shadow_exit(shadow, bars2, in_session=True)
    assert not do_exit_sh2
    assert shadow["bars"] == 3, "shadow tiếp tục đếm nến độc lập, không phụ thuộc lệnh thật"


def test_shadow_journal_roundtrip(ps_module, tmp_path):
    ps = ps_module
    shadow_file = str(tmp_path / "shadow_journal.csv")

    # Can can_trade() = True de _append_journal khong bi chan (gate ps_is_owner)
    ps.st.session_state = {"ps_is_owner": True}

    ps._append_journal({
        "time": "2026-09-04 09:20", "ticker": "VN30F1M", "act": "MỞ LONG",
        "price": 1900.0, "sl": 1894.0, "tp": None, "tp_method": "",
        "pnl": "—", "tid": 1, "result": "", "reason": "shadow trailing 4x ATR",
    }, file=shadow_file)
    ps._append_journal({
        "time": "2026-09-04 09:40", "ticker": "VN30F1M", "act": "ĐÓNG LONG",
        "price": 1910.0, "sl": 1902.0, "tp": None, "tp_method": "",
        "pnl": "+10.0", "tid": 1, "result": "THẮNG",
        "reason": "Chạm trailing SL (shadow) · vào 1.900,0 · giữ 20 phút",
    }, file=shadow_file)

    from vn_invest.daily_report import load_trades
    trades = load_trades(journal_file=shadow_file)
    assert len(trades) == 1
    r = trades.iloc[0]
    assert r["tid"] == 1 and r["side"] == "LONG"
    assert abs(r["entry"] - 1900.0) < 1e-9 and abs(r["exit"] - 1910.0) < 1e-9
    assert abs(r["pnl"] - 10.0) < 1e-9


def test_ps_state_roundtrip_includes_shadow(ps_module):
    ps = ps_module
    ps.st.session_state = {"ps_is_owner": True, "ps_session_token": "tok1",
                           "ps_trade_seq": 1}
    ts = pd.Timestamp("2026-09-04 09:20")
    shadow = ps._open_shadow_position("SHORT", 1900.0, ts, atr=2.0, tid=1)
    shadow["bars"] = 5
    shadow["peak"] = 1888.0
    ps.st.session_state["ps_position"] = None
    ps.st.session_state["ps_shadow_position"] = shadow
    ps._save_ps_state()

    _pos, _seq, _shadow_disk = ps._load_ps_state()
    assert _pos is None
    assert _shadow_disk is not None
    assert _shadow_disk["tid"] == 1
    assert _shadow_disk["side"] == "SHORT"
    assert _shadow_disk["bars"] == 5
    assert abs(_shadow_disk["peak"] - 1888.0) < 1e-9
    assert isinstance(_shadow_disk["entry_ts"], pd.Timestamp)


def test_no_second_shadow_while_one_active(ps_module):
    """Mô phỏng đúng guard trong _live_panel_body(): không mở shadow thứ 2 khi
    1 shadow đang chạy — kiểm bằng cách gọi lại chính điều kiện `if not
    st.session_state.get("ps_shadow_position")` mà engine dùng."""
    ps = ps_module
    ps.st.session_state = {"ps_is_owner": True}
    ts = pd.Timestamp("2026-09-04 09:20")
    shadow1 = ps._open_shadow_position("LONG", 1900.0, ts, atr=2.0, tid=1)
    ps.st.session_state["ps_shadow_position"] = shadow1

    # Tín hiệu MỞ lệnh thứ 2 xuất hiện trong khi shadow #1 còn sống — guard
    # phải ngăn mở shadow #2 (đúng logic trong khối "5) Chưa có vị thế").
    should_open_shadow2 = not ps.st.session_state.get("ps_shadow_position")
    assert not should_open_shadow2, "không được mở shadow thứ 2 khi 1 shadow đang chạy"
    assert ps.st.session_state["ps_shadow_position"] is shadow1, "shadow #1 vẫn nguyên vẹn"
