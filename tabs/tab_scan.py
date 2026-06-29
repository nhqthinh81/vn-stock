"""TAB 3 — QUICK SCAN: Quét tín hiệu toàn thị trường."""
import threading
from datetime import datetime as _dt

import pandas as pd
import streamlit as st

from vn_invest.lstm import model_ready
from vn_invest.screener import (
    load_cache, load_cache_meta, scan_ami_watchlist, scan_ami_symbol,
    get_ami_watchlist, get_all_ami_symbols, get_ami_scan_age,
    refresh_prices, refresh_signals_from_ami, filter_cache, scan_symbol,
    _BAD_STATUSES,
)
from vn_invest.config import RESTRICTED_SYMBOLS


def render(ctx: dict) -> None:
    source = ctx["source"]

    st.header("Quick Scan — Toàn thị trường")

    if "scan_cache" not in st.session_state:
        st.session_state.scan_cache = load_cache()
    if "scan_auto_refresh" not in st.session_state:
        st.session_state.scan_auto_refresh = False
    if "scan_auto_interval" not in st.session_state:
        st.session_state.scan_auto_interval = 10

    import os as _os_qs
    _ami_scan_path_qs = _os_qs.getenv("AMIBROKER_SCAN_CSV", r"C:\AmibrokerData\scan_result.csv")
    if _os_qs.path.exists(_ami_scan_path_qs):
        _ami_mtime_qs = _os_qs.path.getmtime(_ami_scan_path_qs)
        _last_mtime   = st.session_state.get("scan_ami_mtime", 0)
        if _ami_mtime_qs > _last_mtime:
            _auto_pb = st.progress(0, text="📡 Phát hiện Amibroker export mới — đang tải...")
            def _auto_cb(i, total, sym):
                _auto_pb.progress(min(i / max(total, 1), 1.0), text=f"Đang load {sym} ({i}/{total})")
            st.session_state.scan_cache = refresh_signals_from_ami(progress_callback=_auto_cb)
            st.session_state.scan_ami_mtime = _ami_mtime_qs
            _auto_pb.empty()
            st.toast(f"✅ Đã tải lại {len(st.session_state.scan_cache)} mã từ Amibroker export mới", icon="📡")

    _ami_list      = get_ami_watchlist()
    _all_ami_syms  = get_all_ami_symbols()
    _ami_scan_age  = get_ami_scan_age()
    _lstm_avail    = model_ready()

    try:
        import os as _os
        from pathlib import Path as _Path
        _APP_DIR = _Path(__file__).parent.parent
        _ami_scan_path = _os.getenv("AMIBROKER_SCAN_CSV", r"C:\AmibrokerData\scan_result.csv")
        _cache_path = str(_APP_DIR / "data" / "scores_cache.json")
        if _os.path.exists(_ami_scan_path) and _os.path.exists(_cache_path):
            _ami_mtime   = _os.path.getmtime(_ami_scan_path)
            _cache_mtime = _os.path.getmtime(_cache_path)
            _cache_sym_count = len(st.session_state.scan_cache)
            _ami_sym_count   = len(_ami_list)
            _all_sym_count   = len(_all_ami_syms)
            if _ami_mtime > _cache_mtime:
                _diff_min = int((_ami_mtime - _cache_mtime) / 60)
                _diff_label = f"{_diff_min} phút" if _diff_min < 60 else f"{_diff_min//60} giờ {_diff_min%60} phút"
                st.warning(
                    f"⚠️ **Amibroker đã Explore lại** ({_diff_label} trước khi cache được tạo). "
                    f"Cache hiện có **{_cache_sym_count} mã** — "
                    f"Scan đã lọc có **{_ami_sym_count} mã**, Toàn bộ history có **{_all_sym_count} mã**. "
                    f"Nhấn **⚡ Scan** để cập nhật tín hiệu (~20s).",
                    icon="🔁"
                )
    except Exception:
        pass

    scan_opt_c1, scan_opt_c2, scan_opt_c3, scan_opt_c4, scan_opt_c5 = st.columns([2, 1, 1, 1, 1])
    with scan_opt_c2:
        _use_lstm_scan = st.checkbox(
            "Kèm AI Score", value=_lstm_avail,
            disabled=not _lstm_avail,
            help="Chạy LSTM cho mỗi mã khi scan (~2-3s thêm/mã nếu không có GPU)"
        )
    with scan_opt_c3:
        _live_mode = st.toggle("🔴 Live (vnstock)", value=False, key="scan_live_mode",
                               help="Tính lại RSI/MACD/Dist từ giá vnstock realtime phiên hôm nay. VolRatio giữ từ AMI EOD.")
    with scan_opt_c4:
        _auto_refresh_price = st.toggle("⏱ Tự làm mới giá", value=False, key="scan_auto_toggle")
    with scan_opt_c5:
        _auto_interval_min = st.selectbox("Mỗi (phút)", [5, 10, 15, 30],
                                          index=1, key="scan_interval",
                                          disabled=not _auto_refresh_price)

    col_a, col_b, col_c, col_d = st.columns([2, 2, 2, 2])

    with col_a:
        if st.button("🔄 Làm mới (giá + signal)", use_container_width=True):
            _total_ami = len(load_cache())
            _prog = st.progress(0, text="Đang scan từ Amibroker...")
            def _cb(i, total, sym):
                _prog.progress(min(i / max(total, 1), 1.0), text=f"Scan {sym} ({i}/{total})")
            st.session_state.scan_cache = refresh_signals_from_ami(progress_callback=_cb)
            _prog.empty()
            st.success(f"Đã cập nhật signal + giá cho {len(st.session_state.scan_cache)} mã")

    with col_b:
        _age_note = f" · {_ami_scan_age}" if _ami_scan_age else ""
        _btn_filtered = st.button(
            f"⚡ Scan đã lọc ({len(_ami_list)} mã{_age_note})",
            use_container_width=True,
            help="Scan các mã trong scan_result.csv — đã qua bộ lọc Amibroker Explorer (~20s)",
        )

    with col_c:
        _btn_all = st.button(
            f"🌐 Scan tất cả ({len(_all_ami_syms)} mã)",
            use_container_width=True,
            help="Scan toàn bộ mã có data trong history_by_ticker/ (~20s)",
        )

    _scan_pb_ph   = st.empty()
    _scan_txt_ph  = st.empty()

    if _btn_filtered or _btn_all:
        _syms_to_scan = _ami_list if _btn_filtered else _all_ami_syms
        _total_scan   = len(_syms_to_scan)
        _scan_pb_ph.progress(0)
        _scan_txt_ph.info(f"⏳ Đang scan 0/{_total_scan} mã... (~20 giây, vui lòng chờ)")
        _lock_s = threading.Lock(); _cnt_s = [0]
        def on_progress_scan(i, total, sym):
            with _lock_s:
                _cnt_s[0] += 1
                pct = min(int(_cnt_s[0] / total * 100), 100)
                _scan_pb_ph.progress(pct)
                _scan_txt_ph.info(f"⏳ Scan {sym}... ({_cnt_s[0]}/{total} — {pct}%)")
        st.session_state.scan_cache = scan_ami_watchlist(
            symbols=_syms_to_scan, with_lstm=_use_lstm_scan, progress_callback=on_progress_scan
        )
        _scan_pb_ph.empty()
        ai_note = " (kèm AI Score)" if _use_lstm_scan else ""
        _scan_txt_ph.success(
            f"✅ Hoàn tất! Scan {len(st.session_state.scan_cache)}/{_total_scan} mã{ai_note}."
        )
        st.rerun()

    with col_d:
        scan_single = st.text_input("Scan 1 mã", placeholder="VNM", key="scan_single_input").upper().strip()
        if st.button("Scan mã này", use_container_width=True) and scan_single:
            with st.spinner(f"Đang scan {scan_single}..."):
                rec = scan_ami_symbol(scan_single, with_lstm=_use_lstm_scan) or scan_symbol(scan_single, source=source)
            if rec: st.session_state["single_scan_result"] = rec
            else:   st.error("Không lấy được dữ liệu")

    _meta = load_cache_meta()
    _scanned_str   = _meta.get("scanned_at")
    _refreshed_str = _meta.get("price_refreshed_at")

    def _age_label(ts_str) -> str:
        if not ts_str:
            return "chưa có"
        try:
            dt   = _dt.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            mins = int((_dt.now() - dt).total_seconds() / 60)
            if mins < 1:   return "vừa xong"
            if mins < 60:  return f"{mins} phút trước"
            hrs = mins // 60
            if hrs < 24:   return f"{hrs} giờ trước"
            return f"{hrs // 24} ngày trước"
        except Exception:
            return ts_str

    _info_parts = [f"📊 Signal scan: **{_age_label(_scanned_str)}**"]
    if _refreshed_str:
        _info_parts.append(f"⚡ Giá làm mới: **{_age_label(_refreshed_str)}**")
    if _meta.get("count"):
        _info_parts.append(f"**{_meta['count']} mã**")
    st.caption(" &nbsp;|&nbsp; ".join(_info_parts))

    if _scanned_str and _refreshed_str:
        try:
            _t_scan    = _dt.strptime(_scanned_str,   "%Y-%m-%d %H:%M:%S")
            _t_refresh = _dt.strptime(_refreshed_str, "%Y-%m-%d %H:%M:%S")
            if _t_refresh > _t_scan:
                st.warning(
                    "⚠️ **Giá đã được làm mới** sau lần Scan cuối — "
                    "cột Giá phản ánh giá hiện tại nhưng **Signal / RSI / MACD vẫn từ lần Scan trước**. "
                    "Nhấn '⚡ Scan Amibroker' để tính lại tín hiệu."
                )
        except Exception:
            pass

    if "single_scan_result" in st.session_state:
        rec = st.session_state["single_scan_result"]
        st.divider()
        _si = {"BUY-A":"🟢","BUY-B":"🟩","HOLD":"🟡","SELL-B":"🟠","SELL-A":"🔴"}
        _ri = {"Low":"🟢","Medium":"🟡","High":"🔴"}
        _pi = {"Accumulation":"📦","Markup":"📈","Distribution":"📤","Markdown":"📉","Neutral":"➖"}
        sig_s = rec.get("signal",""); risk_s = rec.get("risk",""); phase_s = rec.get("phase","")
        st.markdown(f"### {_si.get(sig_s,'')} Kết quả scan — **{rec['symbol']}**")
        r1c1,r1c2,r1c3,r1c4 = st.columns(4)
        r1c1.metric("Giá đóng cửa",   f"{rec.get('close',0):,.2f}")
        r1c2.metric("RSI (14)",        f"{rec.get('rsi',0):.1f}")
        r1c3.metric("Điểm kỹ thuật",  f"{rec.get('tech_score',0):.1f} / 100")
        r1c4.metric("Dist EMA34",      f"{rec.get('dist_ema34_pct',0):.2f}%")
        r2c1,r2c2,r2c3,r2c4 = st.columns(4)
        r2c1.metric("Tín hiệu",  f"{_si.get(sig_s,'')} {sig_s}")
        r2c2.metric("Rủi ro",    f"{_ri.get(risk_s,'')} {risk_s}")
        r2c3.metric("Giai đoạn", f"{_pi.get(phase_s,'')} {phase_s}")
        r2c4.metric("MACD Hist", f"{rec.get('macd_hist',0):.4f}")

    cache_data = st.session_state.scan_cache
    st.divider()

    filter_cols = st.columns(5)
    f_signal   = filter_cols[0].selectbox("Tín hiệu Python", ["Tất cả","BUY-A","BUY-B","HOLD","SELL-B","SELL-A"])
    f_risk     = filter_cols[1].selectbox("Rủi ro",          ["Tất cả","Low","Medium","High"])
    f_phase    = filter_cols[2].selectbox("Giai đoạn",       ["Tất cả","Accumulation","Markup","Distribution","Markdown","Neutral"])
    f_ai       = filter_cols[3].selectbox("AI Score",        ["Tất cả","≥ 70 (Mạnh)","≥ 50 (Tích cực)","≤ 30 (Yếu)","Có AI Score"])
    f_ami_rec  = filter_cols[4].selectbox("Ami Rec",         ["Tất cả","STRONG BUY","ACCUMULATE","WATCHING","RISK SELL","TOP SELL"])

    filter_cols2 = st.columns(3)
    f_setup    = filter_cols2[0].selectbox("Setup (Ami)", ["Tất cả","FLAT BASE","VCP TIGHT","PKT PIVOT","PULLBACK","PWR-PLAY","GAP UP"])
    f_forecast = filter_cols2[1].selectbox("Forecast (Ami)", ["Tất cả","BULL DIV","BEAR DIV","BB BOT REV"])
    f_pattern  = filter_cols2[2].selectbox("Mẫu hình giá", ["Tất cả","Có mẫu bull","Có mẫu bear","Có mẫu neutral","Không có mẫu"])

    show_restricted = st.checkbox("Hiện mã bị hạn chế/cảnh báo", value=False,
                                  help="Mặc định ẩn mã restricted/suspended/warning khỏi kết quả")

    if _live_mode:
        if "live_cache" not in st.session_state or st.session_state.get("live_cache_base") != id(st.session_state.scan_cache):
            from vn_invest.screener import apply_live_bar_to_cache as _apply_live
            from vnstock import Trading as _Trading

            _live_syms = [r["symbol"] for r in st.session_state.scan_cache]
            _live_pb   = st.progress(0, text="🔴 Đang lấy giá realtime (batch)...")
            try:
                _t_board  = _Trading(source="KBS", symbol="VNI")
                _board_df = _t_board.price_board(symbols_list=_live_syms)
                _live_pb.progress(0.7, text="🔴 Đang tính RSI/Dist/Signal...")
                _merged   = _apply_live(st.session_state.scan_cache, _board_df)
                _n_ok     = sum(1 for r in _merged if r.get("live_updated"))
            except Exception:
                _merged = list(st.session_state.scan_cache)
                _n_ok   = 0
            _live_pb.empty()
            st.session_state["live_cache"]      = _merged
            st.session_state["live_cache_base"] = id(st.session_state.scan_cache)
            st.caption(f"🔴 Live: {_n_ok}/{len(_live_syms)} mã · RSI/Dist/Signal xấp xỉ realtime · VolRatio giữ AMI EOD")

        _active_cache = st.session_state["live_cache"]
        st.info("🔴 **Live mode** — RSI/MACD/Dist/Signal từ vnstock realtime · VolRatio giữ từ AMI EOD")
    else:
        _active_cache = st.session_state.scan_cache

    filtered = filter_cache(
        signal=None   if f_signal=="Tất cả"   else f_signal,
        risk=None     if f_risk=="Tất cả"     else f_risk,
        phase=None    if f_phase=="Tất cả"    else f_phase,
        ai_score=None if f_ai=="Tất cả"       else f_ai,
        ami_rec=None  if f_ami_rec=="Tất cả"  else f_ami_rec,
        setup=None    if f_setup=="Tất cả"    else f_setup,
        forecast=None if f_forecast=="Tất cả" else f_forecast,
        pattern=None  if f_pattern=="Tất cả"  else f_pattern,
        data=_active_cache,
        exclude_restricted=not show_restricted,
    )

    _full_cache = _active_cache
    _df_full    = pd.DataFrame(_active_cache) if _active_cache else pd.DataFrame()
    _has_ai_full = "ai_score" in _df_full.columns and _df_full["ai_score"].notna().any() if not _df_full.empty else False

    from vn_invest.paper_trading import add_trade as _pt_add
    _buya_rows = [r for r in (_active_cache or []) if r.get("signal") == "BUY-A"]
    _pt_col1, _pt_col2 = st.columns([3, 1])
    with _pt_col1:
        if _buya_rows:
            st.caption(f"📌 Hiện có **{len(_buya_rows)} mã BUY-A** trong cache: "
                       + ", ".join(r["symbol"] for r in _buya_rows[:10])
                       + ("..." if len(_buya_rows) > 10 else ""))
        else:
            st.caption("📌 Không có mã BUY-A trong cache hiện tại.")
    with _pt_col2:
        if st.button("📌 Ghi BUY-A hôm nay", use_container_width=True,
                     disabled=not _buya_rows,
                     help="Ghi toàn bộ mã BUY-A hiện tại vào Paper Trading để theo dõi T+5 tuần"):
            _added = []
            for _r in _buya_rows:
                _pt_add(
                    symbol=_r["symbol"],
                    entry_price=float(_r.get("close") or 0),
                    tech_score=float(_r.get("tech_score") or 0),
                    rsi=float(_r.get("rsi") or 0),
                    signal="BUY-A",
                )
                _added.append(_r["symbol"])
            st.success(f"Đã ghi {len(_added)} mã vào Paper Trading: {', '.join(_added)}")

    # ── Khuyến nghị nhanh ─────────────────────────────────────────────────────
    if not _df_full.empty:
        st.subheader("⚡ Khuyến Nghị Nhanh")

        _BAD_PHASES  = {"Distribution", "Markdown"}
        _BUY_SIGNALS = {"BUY-A", "BUY-B"}
        _DOWNTREND_PHASES = {"Markdown"}

        _STRONG_BEAR_PATTERNS = {
            "three black crows", "evening star", "bearish engulfing",
            "head & shoulders", "double top", "descending triangle",
            "shooting star", "hanging man",
        }

        def _has_strong_bear(row) -> bool:
            pats = " ".join([
                str(row.get("candle_patterns") or ""),
                str(row.get("chart_patterns")  or ""),
            ]).lower()
            return any(p in pats for p in _STRONG_BEAR_PATTERNS)

        def _rsi_ok_for_buy(rsi: float) -> bool:
            return rsi <= 30 or (41 <= rsi < 70)

        def _con_label(row):
            ai    = row.get("ai_score")
            kt    = float(row.get("tech_score", 50) or 50)
            risk  = row.get("risk",   "Medium") or "Medium"
            phase = row.get("phase",  "Neutral") or "Neutral"
            sig   = row.get("signal", "HOLD") or "HOLD"
            dist  = float(row.get("dist_ema34_pct", 0) or 0)
            rsi   = float(row.get("rsi", 50) or 50)
            has_ai = ai is not None and not (isinstance(ai, float) and pd.isna(ai))

            macd_pos = float(row.get("macd_hist", 0) or 0) > 0
            rsi_ok = _rsi_ok_for_buy(rsi)
            trend_ok = bool(row.get("macd_rising")) or bool(row.get("price_above_sma5_3d"))

            wmt       = int(row.get("weekly_macd_trend") or 0)
            ma_al     = int(row.get("ma_aligned") or 0)
            trend20   = float(row.get("price_trend_20d") or 0)
            longterm_ok = (wmt >= 0) and (ma_al >= 0)
            longterm_ok_relaxed = (wmt >= 0) or (ma_al > 0 and trend20 > -5)

            rev_type     = row.get("reversal_type", "none") or "none"
            _rs = row.get("reversal_strength", 0)
            rev_strength = 0 if pd.isna(_rs) else int(_rs or 0)
            is_bull_rev  = rev_type == "bullish"  and rev_strength >= 40
            is_bear_rev  = rev_type == "bearish"  and rev_strength >= 40

            in_downtrend = phase in _DOWNTREND_PHASES or ma_al < 0

            bear_block = _has_strong_bear(row) or dist < -20 or not longterm_ok_relaxed or in_downtrend
            buy_ok     = macd_pos and rsi_ok and trend_ok and not bear_block

            if has_ai:
                ai = float(ai)
                if (ai >= 70 and kt >= 60
                        and risk != "High"
                        and phase not in _BAD_PHASES
                        and sig in _BUY_SIGNALS
                        and buy_ok
                        and wmt >= 0 and ma_al > 0):
                    return "✅ Mua mạnh"
                if ai >= 50 and kt >= 50 and buy_ok:              return "🟢 Tích cực"
                if is_bull_rev and not bear_block and risk != "High": return "🔄 Đảo Chiều"
                if ai <= 30 and kt <= 35:                          return "🔴 Bán"
                if ai <= 40 and kt <= 45:                          return "🟠 Thận trọng"
                if bear_block or is_bear_rev:                      return "🟠 Thận trọng"
                if ai >= 70 and kt < 50:                           return "⚠️ AI↑ KT↓"
                if ai < 40  and kt >= 60:                          return "⚠️ AI↓ KT↑"
                return "🟡 Trung tính"
            else:
                if (sig == "BUY-A" and risk != "High"
                        and phase not in _BAD_PHASES
                        and buy_ok
                        and wmt >= 0 and ma_al > 0):
                    return "✅ Mua mạnh"
                if sig in ("BUY-A", "BUY-B") and phase not in _BAD_PHASES and buy_ok:
                    return "🟢 Tích cực"
                if is_bull_rev and not bear_block and risk != "High": return "🔄 Đảo Chiều"
                if sig in ("SELL-A", "SELL-B"):                    return "🔴 Bán"
                if risk == "High" or bear_block or is_bear_rev:    return "🟠 Thận trọng"
                return "🟡 Trung tính"

        _mask_restricted = _df_full["symbol"].isin(RESTRICTED_SYMBOLS)
        if "stock_status" in _df_full.columns:
            _mask_restricted |= _df_full["stock_status"].isin(_BAD_STATUSES)
        _df_rec = _df_full[~_mask_restricted].copy()

        _rec_cache_key = id(st.session_state.scan_cache)
        if st.session_state.get("_rec_label_key") != _rec_cache_key:
            _df_rec["_con"] = _df_rec.apply(_con_label, axis=1)
            st.session_state["_rec_label_cache"] = dict(zip(_df_rec["symbol"], _df_rec["_con"]))
            st.session_state["_rec_label_key"]   = _rec_cache_key
        else:
            _df_rec["_con"] = _df_rec["symbol"].map(st.session_state["_rec_label_cache"]).fillna("🟡 Trung tính")
        _sort_col = "ai_score" if "ai_score" in _df_rec.columns else "tech_score"
        _buy_strong = _df_rec[_df_rec["_con"] == "✅ Mua mạnh"].nlargest(5, _sort_col)
        _buy_pos    = _df_rec[_df_rec["_con"] == "🟢 Tích cực"].nlargest(5, _sort_col)
        _reversal   = _df_rec[_df_rec["_con"] == "🔄 Đảo Chiều"].nlargest(5, "reversal_strength") \
                      if "reversal_strength" in _df_rec.columns else pd.DataFrame()
        _sell       = _df_rec[_df_rec["_con"] == "🔴 Bán"].nsmallest(5, _sort_col)
        _warning    = _df_rec[_df_rec["_con"].isin(["⚠️ AI↑ KT↓","⚠️ AI↓ KT↑","🟠 Thận trọng"])].nlargest(5, _sort_col)

        def _rec_cards(df_grp, bg_color, label):
            if df_grp.empty:
                st.caption(f"_Không có mã {label}_")
                return
            cols = st.columns(min(len(df_grp), 5))
            for col, (_, row) in zip(cols, df_grp.iterrows()):
                def _fv(v, default=0.0):
                    try:
                        f = float(v)
                        return f if f == f else default
                    except (TypeError, ValueError):
                        return default

                sym        = row["symbol"]
                ai         = _fv(row.get("ai_score"))
                kt         = _fv(row.get("tech_score"))
                close      = _fv(row.get("close"))
                rsi        = _fv(row.get("rsi"))
                dist       = _fv(row.get("dist_ema34_pct"))
                phase      = str(row.get("phase") or "")
                risk_s     = str(row.get("risk")  or "")
                candle_pats = str(row.get("candle_patterns") or "")
                candle_tf   = str(row.get("candle_timeframe") or "none")
                chart_pats  = str(row.get("chart_patterns")  or "")
                reason      = str(row.get("reason") or "")

                _ps     = {"Accumulation":"Tích lũy","Markup":"Tăng","Distribution":"Phân phối",
                           "Markdown":"Giảm","Neutral":"Trung tính"}.get(phase, phase)
                _ri_col = {"Low":"#00e676","Medium":"#ffd740","High":"#ff1744"}.get(risk_s,"#aaaaaa")
                _ai_lbl = f"{ai:.0f}" if ai > 0 else "—"

                if candle_pats:
                    if candle_tf == "weekly":
                        _cpat_lbl  = "Nến [Tuần — tin cậy thấp hơn daily]"
                        _cpat_color = "#ffb74d"
                    else:
                        _cpat_lbl  = "Nến [Daily]"
                        _cpat_color = "#ffe082"
                    _cpat_html = (f'<div style="font-size:0.76em;color:{_cpat_color};margin-top:3px">'
                                  f'{_cpat_lbl}: {candle_pats}</div>')
                else:
                    _cpat_html = ""

                _gpat_html = (f'<div style="font-size:0.76em;color:#80cbc4;margin-top:2px">'
                              f'Chart: {chart_pats}</div>') if chart_pats else ""
                _bullets   = [b for b in reason.split(" • ") if b.strip()] if reason else []
                _rea_short = " • ".join(_bullets[:2])
                _rea_html  = (f'<div style="font-size:0.74em;color:#b0bec5;margin-top:3px;line-height:1.45">'
                              f'{_rea_short}</div>') if _rea_short else ""

                col.markdown(f"""
<div style="background:{bg_color};border-radius:10px;padding:12px 14px;margin:2px 0;color:#ffffff">
<b style="font-size:1.1em">{sym}</b><br>
<span style="font-size:0.85em;color:#cccccc">{close:,.0f} &nbsp;|&nbsp; RSI {rsi:.0f}</span><br>
<span style="font-size:0.82em;color:#aaaaaa">Dist {dist:+.1f}% &nbsp;·&nbsp; {_ps}</span>
{_cpat_html}{_gpat_html}
<hr style="margin:5px 0;border-color:rgba(255,255,255,0.15)">
<span>🤖 AI <b>{_ai_lbl}</b> &nbsp; KT <b>{kt:.0f}</b></span>
&nbsp;<span style="font-size:0.8em;color:{_ri_col}">● {risk_s}</span>
{_rea_html}
</div>""", unsafe_allow_html=True)
                if _bullets:
                    with col.expander("Chi tiet phan tich"):
                        for bullet in _bullets:
                            st.markdown(f"- {bullet}")

        kn_t1, kn_t2, kn_t3, kn_t4, kn_t5 = st.tabs([
            f"✅ Mua mạnh ({len(_buy_strong)})",
            f"🟢 Tích cực ({len(_buy_pos)})",
            f"🔄 Đảo Chiều ({len(_reversal)})",
            f"🔴 Bán ({len(_sell)})",
            f"⚠️ Thận trọng ({len(_warning)})",
        ])
        with kn_t1: _rec_cards(_buy_strong, "#1a3a2a", "Mua mạnh")
        with kn_t2: _rec_cards(_buy_pos,    "#1a2e1a", "Tích cực")
        with kn_t3:
            if not _reversal.empty:
                st.caption("⚠️ Tín hiệu đảo chiều tiềm năng — chưa xác nhận, rủi ro cao hơn Mua mạnh. Chỉ tham khảo.")
            _rec_cards(_reversal, "#1a2535", "Đảo Chiều")
        with kn_t4: _rec_cards(_sell,        "#3a1a1a", "Bán")
        with kn_t5: _rec_cards(_warning,     "#2a2a1a", "Thận trọng")

        st.divider()

    # ── Bảng chi tiết ─────────────────────────────────────────────────────────
    if not filtered:
        st.info("Cache rỗng. Nhấn 'Scan Amibroker' để bắt đầu.")
    else:
        df_scan = pd.DataFrame(filtered)
        has_ai  = "ai_score" in df_scan.columns and df_scan["ai_score"].notna().any()

        if has_ai and f_ai != "Tất cả":
            if f_ai == "≥ 70 (Mạnh)":      df_scan = df_scan[df_scan["ai_score"] >= 70]
            elif f_ai == "≥ 50 (Tích cực)": df_scan = df_scan[df_scan["ai_score"] >= 50]
            elif f_ai == "≤ 30 (Yếu)":      df_scan = df_scan[df_scan["ai_score"] <= 30]
            elif f_ai == "Có AI Score":      df_scan = df_scan[df_scan["ai_score"].notna()]

        if f_ami_rec != "Tất cả" and "ami_rec_label" in df_scan.columns:
            df_scan = df_scan[df_scan["ami_rec_label"] == f_ami_rec]

        if f_setup != "Tất cả" and "ami_setup" in df_scan.columns:
            df_scan = df_scan[df_scan["ami_setup"] == f_setup].copy()

        if f_forecast != "Tất cả" and "ami_forecast" in df_scan.columns:
            df_scan = df_scan[df_scan["ami_forecast"] == f_forecast].copy()

        if f_pattern != "Tất cả" and "chart_patterns" in df_scan.columns:
            cp = df_scan["chart_patterns"].fillna("")
            if f_pattern == "Có mẫu bull":
                df_scan = df_scan[cp.str.contains(r"\[bull,", na=False)].copy()
            elif f_pattern == "Có mẫu bear":
                df_scan = df_scan[cp.str.contains(r"\[bear,", na=False)].copy()
            elif f_pattern == "Có mẫu neutral":
                df_scan = df_scan[cp.str.contains(r"\[neutral,", na=False)].copy()
            elif f_pattern == "Không có mẫu":
                df_scan = df_scan[cp.eq("")].copy()

        _all_rows = st.session_state.scan_cache
        _n_restricted = sum(
            1 for r in _all_rows
            if r.get("stock_status", "normal") in _BAD_STATUSES
        ) if not show_restricted else 0
        if _n_restricted:
            _restricted_names = ", ".join(
                r["symbol"] for r in _all_rows
                if r.get("stock_status", "normal") in _BAD_STATUSES
            )
            st.warning(
                f"⛔ Đã ẩn **{_n_restricted} mã** bị hạn chế/cảnh báo khỏi kết quả: "
                f"`{_restricted_names}` — tick 'Hiện mã bị hạn chế' để xem."
            )

        if not has_ai and model_ready():
            st.caption(f"Hiển thị {len(df_scan)} mã — tick 'Kèm AI Score' rồi Scan lại để lọc theo AI Score")
        else:
            st.caption(f"Hiển thị {len(df_scan)} mã" + (" | AI Score: LSTM" if has_ai else ""))

        df_scan = df_scan.reset_index(drop=True).copy()
        df_scan["consensus"] = [_con_label(df_scan.iloc[_i]) for _i in range(len(df_scan))]

        _STATUS_BADGE = {"restricted": "⛔ Hạn chế", "suspended": "🚫 Tạm ngừng",
                         "warning": "⚠️ Cảnh báo", "delisted": "❌ Hủy niêm yết"}
        if "stock_status" in df_scan.columns:
            df_scan["_warn"] = df_scan["stock_status"].map(
                lambda s: _STATUS_BADGE.get(s, "") if pd.notna(s) else ""
            )
        else:
            df_scan["_warn"] = ""

        display_cols = [c for c in [
            "symbol","_warn","close","ami_date","rsi","dist_ema34_pct",
            "atr_pct","bb_width_pct","volume_ratio",
            "ai_score","tech_score","consensus","signal","risk","phase",
            "ami_rec_label","ami_score","ami_setup","ami_forecast",
            "chart_patterns",
        ] if c in df_scan.columns]
        df_display = df_scan[display_cols].rename(columns={
            "symbol":"Mã","_warn":"Trạng thái","close":"Giá","ami_date":"Ngày DL",
            "rsi":"RSI","dist_ema34_pct":"Dist%",
            "atr_pct":"ATR%","bb_width_pct":"BB%","volume_ratio":"VolR",
            "ai_score":"AI","tech_score":"KT","consensus":"Đồng thuận",
            "signal":"Tín hiệu","risk":"Rủi ro","phase":"Giai đoạn",
            "ami_rec_label":"Ami Rec","ami_score":"AmiSc",
            "ami_setup":"Setup","ami_forecast":"Forecast",
            "chart_patterns":"Mẫu hình giá",
        })
        if df_display["Trạng thái"].eq("").all():
            df_display = df_display.drop(columns=["Trạng thái"])
        st.markdown("""<style>
[data-testid="stDataFrame"] .ag-cell {
    white-space: normal !important;
    word-break: break-word !important;
    line-height: 1.5 !important;
}
</style>""", unsafe_allow_html=True)
        st.dataframe(df_display, use_container_width=True, hide_index=True,
            column_config={
                "Mã":        st.column_config.TextColumn(width="small"),
                "Giá":       st.column_config.NumberColumn(format="%,.2f", width="small"),
                "Ngày DL":   st.column_config.TextColumn(width="small",
                                 help="Ngày dữ liệu từ Amibroker (DD/MM/YYYY)"),
                "RSI":       st.column_config.NumberColumn(format="%.1f",  width="small"),
                "Dist%":     st.column_config.NumberColumn(format="%.1f%%", width="small", help="Dist EMA34%"),
                "ATR%":      st.column_config.NumberColumn(format="%.1f%%", width="small",
                                 help="ATR/Giá — volatility thực tế. >3%: biến động cao"),
                "BB%":       st.column_config.NumberColumn(format="%.1f%%", width="small",
                                 help="Bollinger Band Width. <5%: squeeze, >15%: đang giãn"),
                "VolR":      st.column_config.NumberColumn(format="%.2f",   width="small",
                                 help="Khối lượng / SMA20(KL). >1.5: xác nhận tín hiệu mạnh"),
                "KT":        st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "AI":        st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Đồng thuận":st.column_config.TextColumn(width="small"),
                "Tín hiệu":  st.column_config.TextColumn(width="small"),
                "Rủi ro":    st.column_config.TextColumn(width="small"),
                "Giai đoạn": st.column_config.TextColumn(width="small"),
                "Ami Rec":   st.column_config.TextColumn(width="small"),
                "AmiSc":     st.column_config.NumberColumn(format="%.0f", width="small"),
                "Setup":     st.column_config.TextColumn(width="small"),
                "Forecast":  st.column_config.TextColumn(width="small"),
                "Mẫu hình giá": st.column_config.TextColumn(width="large"),
            })

        with st.expander("🔬 Phân tích nâng cao (kéo-thả như Tableau)", expanded=False):
            try:
                from pygwalker.api.streamlit import StreamlitRenderer

                @st.cache_resource
                def _get_pyg_walker(data_hash: int, cols: tuple):
                    return StreamlitRenderer(
                        df_scan[list(cols)],
                        kernel_computation=True,
                        appearance="dark",
                    )

                _pyg_cols = tuple(df_scan.columns.tolist())
                _pyg_hash = hash(_pyg_cols + (len(df_scan),))
                _get_pyg_walker(_pyg_hash, _pyg_cols).explorer(default_tab="data")
            except ImportError:
                st.warning("Cài `pygwalker` để dùng tính năng này: `pip install pygwalker`")
            except Exception as e:
                st.error(f"PyGWalker lỗi: {e}")

        st.divider()
        st.subheader("Phân bổ tín hiệu")
        if "signal" in df_scan.columns:
            sig_count = df_scan["signal"].value_counts().reset_index()
            sig_count.columns = ["Tín hiệu","Số mã"]
            st.bar_chart(sig_count.set_index("Tín hiệu"), use_container_width=True)

        st.divider()
        st.subheader("📊 Backtest — Win rate tín hiệu trên dữ liệu VN")
        from vn_invest.backtester import load_results as _load_bt, run_backtest as _run_bt

        _bt_data = _load_bt()
        _bt_c1, _bt_c2, _bt_c3 = st.columns(3)
        _bt_fwd  = _bt_c1.selectbox("Kỳ kiểm định (tuần)", [5, 10, 20], index=1, key="bt_fwd")
        _bt_maxs = _bt_c2.slider("Số mã tối đa", 50, 500, 200, step=50, key="bt_maxs")
        _bt_exit = _bt_c3.selectbox("Exit Strategy", [
            "Hold cứng (baseline)",
            "Trailing Stop 12% ✅ (khuyến nghị)",
            "Trailing Stop 8%",
            "Trailing Stop 5%",
            "TP 15% + Trail 8%",
        ], index=1, key="bt_exit")
        _TRAIL_MAP = {
            "Hold cứng (baseline)":            (None, None),
            "Trailing Stop 12% ✅ (khuyến nghị)": (0.12, None),
            "Trailing Stop 8%":                (0.08, None),
            "Trailing Stop 5%":                (0.05, None),
            "TP 15% + Trail 8%":               (0.08, 0.15),
        }
        _bt_trail, _bt_tp = _TRAIL_MAP[_bt_exit]

        if _bt_data:
            _bt_meta = (f"Đã backtest {_bt_data['symbols_scanned']} mã · "
                       f"{_bt_data['total_signals']:,} tín hiệu · "
                       f"T+{_bt_data['forward_days']} · {_bt_data['computed_at']}")
            st.caption(_bt_meta)

            _alpha  = _bt_data.get("buy_a_alpha")
            _mkt    = _bt_data.get("market_avg_return")
            _edge   = _bt_data.get("signal_edge")
            _filters = _bt_data.get("filters_applied", [])
            _edge_col, _filter_col = st.columns([1, 2])
            with _edge_col:
                if _alpha is not None:
                    _ac = "#00e676" if _alpha > 2 else ("#ffd740" if _alpha > 0 else "#ff5252")
                    _mkt_str = f" | Mkt avg: {_mkt:+.1f}%" if _mkt is not None else ""
                    _edge_str = f" | Edge(BUY-A−SELL-A): {_edge:+.1f}%" if _edge is not None else ""
                    st.markdown(
                        f"""<div style="background:#1e2130;border-left:4px solid {_ac};
                        border-radius:6px;padding:8px 12px">
                        <span style="color:#aaa;font-size:0.8em">BUY-A Alpha (vs market avg)</span><br>
                        <span style="color:{_ac};font-size:1.3em;font-weight:700">{_alpha:+.1f}%</span>
                        <span style="color:#888;font-size:0.75em"> · &gt;2% = BUY-A vượt trội "mua bừa"{_mkt_str}{_edge_str}</span>
                        </div>""", unsafe_allow_html=True)
            with _filter_col:
                if _filters:
                    st.markdown(
                        f"""<div style="background:#1e2130;border-radius:6px;padding:8px 12px">
                        <span style="color:#aaa;font-size:0.8em">Filters: </span>
                        <span style="color:#69f0ae;font-size:0.8em">{' · '.join(_filters)}</span>
                        </div>""", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            _SIG_ORDER = ["BUY-A","BUY-B","HOLD","SELL-B","SELL-A"]
            _SIG_COLOR = {"BUY-A":"#00e676","BUY-B":"#69f0ae",
                          "HOLD":"#ffd740","SELL-B":"#ff9100","SELL-A":"#ff5252"}
            _WIN_LABEL = {"BUY-A":"giá tăng","BUY-B":"giá tăng",
                          "HOLD":"giá ±ngưỡng","SELL-B":"giá giảm","SELL-A":"giá giảm"}
            _bt_cols = st.columns(5)
            for _bi, _sig in enumerate(_SIG_ORDER):
                _s = _bt_data["summary"].get(_sig, {})
                _cnt = _s.get("count", 0)
                _wr  = _s.get("win_rate")
                _avg = _s.get("avg_return")
                _std = _s.get("std_return")
                _clr = _SIG_COLOR[_sig]
                _wlbl = _WIN_LABEL[_sig]
                _bt_cols[_bi].markdown(
                    f"""<div style="background:#1e2130;border-left:4px solid {_clr};
                    border-radius:6px;padding:10px 12px;text-align:center">
                    <div style="color:{_clr};font-weight:700;font-size:0.9em">{_sig}</div>
                    <div style="font-size:1.4em;font-weight:700;color:#fff;margin:4px 0">
                      {"—" if _wr is None else f"{_wr:.0f}%"}</div>
                    <div style="font-size:0.72em;color:#888">win ({_wlbl})</div>
                    <div style="font-size:0.8em;color:#aaa;margin-top:5px">
                      avg <b>{("—" if _avg is None else f"{_avg:+.1f}%")}</b></div>
                    <div style="font-size:0.72em;color:#666">
                      σ {("—" if _std is None else f"{_std:.1f}%")} · {_cnt:,} tín hiệu</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Chưa có kết quả backtest. Nhấn nút bên dưới để chạy lần đầu (~2-5 phút).")

        if st.button("▶ Chạy Backtest", use_container_width=True, key="run_bt"):
            _bt_pb = st.progress(0)
            _bt_txt = st.empty()
            def _bt_cb(i, total, sym):
                pct = int(i / max(total, 1) * 100)
                _bt_pb.progress(pct)
                _bt_txt.caption(f"Backtest {sym}... ({i}/{total})")
            with st.spinner("Đang chạy backtest..."):
                _bt_result = _run_bt(forward_days=_bt_fwd, max_symbols=_bt_maxs,
                                     progress_callback=_bt_cb,
                                     trail_pct=_bt_trail, tp_pct=_bt_tp)
            _bt_pb.empty(); _bt_txt.empty()
            st.success(f"Hoàn thành! {_bt_result['total_signals']:,} tín hiệu "
                       f"từ {_bt_result['symbols_scanned']} mã.")
            st.rerun()

    if _auto_refresh_price:
        import time as _time
        _interval_secs = _auto_interval_min * 60
        if "scan_last_auto_refresh" not in st.session_state:
            st.session_state.scan_last_auto_refresh = 0.0
        _now_ts  = _time.time()
        _elapsed = _now_ts - st.session_state.scan_last_auto_refresh
        _remain  = max(0, int(_interval_secs - _elapsed))
        st.caption(f"⏱ Auto làm mới giá mỗi {_auto_interval_min} phút — "
                   f"lần tới sau **{_remain // 60}:{_remain % 60:02d}**")
        if _elapsed >= _interval_secs:
            with st.spinner("Auto làm mới giá..."):
                st.session_state.scan_cache = refresh_prices(source=source)
            st.session_state.scan_last_auto_refresh = _time.time()
            st.rerun()

    # ── Lọc cơ bản toàn thị trường ──────────────────────────────────────
    st.divider()
    st.subheader("📊 Lọc cơ bản toàn thị trường")

    from vn_invest.fundamental_scanner import (
        load_fundamental_cache, load_checkpoint_meta,
        scan_all_fundamentals, filter_checklist,
    )

    _fund_data, _fund_updated = load_fundamental_cache()
    _fund_running = st.session_state.get("fund_scan_running", False)

    _fi_meta_col, _fi_btn_col = st.columns([3, 1])
    with _fi_meta_col:
        if _fund_updated:
            st.caption(f"📅 Cập nhật lần cuối: **{_fund_updated}** — {len(_fund_data):,} mã có dữ liệu")
        else:
            _ckpt_meta = load_checkpoint_meta()
            if _ckpt_meta:
                st.caption(f"🔄 Đang quét dở (checkpoint): {_ckpt_meta['done']}/{_ckpt_meta['total']} mã "
                           f"— lưu lúc {_ckpt_meta['saved_at']}")
            else:
                st.caption("⚠️ Chưa có dữ liệu cơ bản. Nhấn **Cập nhật** để quét toàn thị trường (~15 phút, hỗ trợ resume).")
    with _fi_btn_col:
        if st.button("🔄 Cập nhật dữ liệu cơ bản",
                     disabled=_fund_running, key="btn_fund_scan",
                     help="Quét ~1,500 mã từ vnstock KBS, tự động resume nếu bị ngắt."):
            st.session_state["fund_scan_running"]  = True
            st.session_state["fund_scan_progress"] = (0, 1, "Đang khởi động...")

            def _run_fund_scan():
                def _cb(i, total, sym):
                    st.session_state["fund_scan_progress"] = (i, total, sym)
                scan_all_fundamentals(progress_callback=_cb, resume=True)
                st.session_state["fund_scan_running"]  = False
                st.session_state["fund_scan_progress"] = None

            threading.Thread(target=_run_fund_scan, daemon=True).start()
            st.rerun()

    if _fund_running:
        _prog = st.session_state.get("fund_scan_progress") or (0, 1, "...")
        _pi, _pt, _ps = _prog
        _pct = _pi / max(_pt, 1)
        st.progress(_pct, text=f"Đang quét **{_ps}** ({_pi:,}/{_pt:,}) — "
                               f"ước tính còn {int((_pt-_pi)*0.6//60)} phút {int((_pt-_pi)*0.6%60)} giây")
        st.button("↻ Làm mới tiến độ", key="btn_fund_refresh")
        # Tải lại dữ liệu mới nhất từ checkpoint nếu có
        _ckpt_meta2 = load_checkpoint_meta()
        if _ckpt_meta2.get("done", 0) > 0:
            st.caption(f"Checkpoint: {_ckpt_meta2['done']:,}/{_ckpt_meta2['total']:,} mã đã xử lý")

    if _fund_data:
        st.divider()
        st.markdown("**Điều chỉnh ngưỡng lọc:**")
        _fc1, _fc2, _fc3, _fc4, _fc5, _fc6 = st.columns(6)
        _fi_pe    = _fc1.number_input("P/E tối đa",      0.0, 200.0, 25.0, 1.0,  key="fi_pe")
        _fi_pb    = _fc2.number_input("P/B tối đa",      0.0,  20.0,  3.0, 0.5,  key="fi_pb")
        _fi_roe   = _fc3.number_input("ROE tối thiểu %", 0.0,  50.0, 15.0, 1.0,  key="fi_roe")
        _fi_de    = _fc4.number_input("Nợ/VCSH tối đa",  0.0,  20.0,  1.5, 0.5,  key="fi_de")
        _fi_nm    = _fc5.number_input("Biên ròng tối thiểu %", -100.0, 100.0, 0.0, 1.0, key="fi_nm")
        _fi_nmin  = _fc6.number_input("Tiêu chí tối thiểu", 1, 5, 4, key="fi_nmin")

        _filtered = filter_checklist(
            _fund_data,
            pe_max=float(_fi_pe), pb_max=float(_fi_pb),
            roe_min=float(_fi_roe), de_max=float(_fi_de),
            net_margin_min=float(_fi_nm), min_pass=int(_fi_nmin),
        )

        st.markdown(f"**{len(_filtered):,} mã** đạt ≥{int(_fi_nmin)}/5 tiêu chí cơ bản "
                    f"(từ tổng {len(_fund_data):,} mã có dữ liệu)")

        if _filtered:
            _fund_rows = []
            for _r in _filtered:
                _chk = _r.get("checks", {})
                _fund_rows.append({
                    "Mã":             _r["symbol"],
                    "Kỳ":             _r.get("period", "—"),
                    "P/E":            f"{_r['pe']:.1f}"         if _r.get("pe")  is not None else "—",
                    "P/B":            f"{_r['pb']:.1f}"         if _r.get("pb")  is not None else "—",
                    "ROE %":          f"{_r['roe']:.1f}"        if _r.get("roe") is not None else "—",
                    "Nợ/VCSH":        f"{_r['de']:.2f}x"       if _r.get("de")  is not None else "—",
                    "Biên ròng %":    f"{_r['net_margin']:.1f}" if _r.get("net_margin") is not None else "—",
                    "Tăng trưởng LN": (f"{_r['pat_growth']:+.1f}%"
                                       if _r.get("pat_growth") is not None else "—"),
                    "Tiêu chí":       f"{_r.get('n_pass',0)}/5",
                })
            _df_fund = pd.DataFrame(_fund_rows)
            st.dataframe(
                _df_fund,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Mã":             st.column_config.TextColumn(width="small"),
                    "Kỳ":             st.column_config.TextColumn(width="small"),
                    "Tiêu chí":       st.column_config.TextColumn(width="small"),
                    "Tăng trưởng LN": st.column_config.TextColumn(width="medium"),
                },
            )
