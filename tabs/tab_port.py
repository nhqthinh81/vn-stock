"""TAB 4 — DANH MỤC: Portfolio P&L + Paper Trading."""
import io
from pathlib import Path

import pandas as pd
import streamlit as st

from vn_invest.portfolio import (
    load_portfolio, load_portfolio_manual, save_portfolio_manual,
    enrich_portfolio, portfolio_summary, sector_allocation,
    fetch_sector_batch,
)
from vn_invest.screener import load_cache


def render(ctx: dict) -> None:
    source = ctx["source"]

    st.header("💼 Danh mục đầu tư")

    _subtab_port, _subtab_paper = st.tabs(["📊 Danh mục thực", "📌 Paper Trading"])

    # ══════════════════════════════════════════════════════════════════════════
    #  SUB-TAB: PAPER TRADING
    # ══════════════════════════════════════════════════════════════════════════
    with _subtab_paper:
        from vn_invest.paper_trading import (
            load_trades as _pt_load, close_trade as _pt_close,
            delete_trade as _pt_delete, update_prices as _pt_update,
            get_stats as _pt_stats,
        )
        import plotly.graph_objects as _go_pt

        _pt_trades = _pt_load()

        # Cập nhật giá hiện tại từ scan_cache
        _pt_price_map: dict = {}
        _pt_cache_src = st.session_state.get("scan_cache") or load_cache()
        if _pt_cache_src:
            _pt_price_map = {r["symbol"]: float(r["close"]) for r in _pt_cache_src
                             if r.get("symbol") and r.get("close")}
        if _pt_price_map and _pt_trades:
            _pt_trades = _pt_update(_pt_price_map)

        _pt_stats_data = _pt_stats(_pt_trades)
        _pt_open   = [t for t in _pt_trades if t["status"] == "open"]
        _pt_closed = [t for t in _pt_trades if t["status"] == "closed"]

        # ── KPI tổng quan ────────────────────────────────────────────────────
        _unrealized     = [t.get("unrealized_pct", 0) or 0 for t in _pt_open]
        _star_open      = [t for t in _pt_open if t.get("is_star") or t.get("signal") == "BUY-A*"]
        _avg_unrl       = sum(_unrealized) / len(_unrealized) if _unrealized else None
        _pos_cnt        = sum(1 for x in _unrealized if x > 0)
        _neg_cnt        = sum(1 for x in _unrealized if x < 0)

        _kk = st.columns(6)
        _kk[0].metric("Đang mở",       f"{len(_pt_open)} (⭐{len(_star_open)})",
                       help="Tổng đang mở (trong đó ⭐ BUY-A*)")
        _kk[1].metric("Đang lãi / lỗ", f"{_pos_cnt} / {_neg_cnt}")
        _kk[2].metric("Return TB (đang mở)", f"{_avg_unrl:+.2f}%" if _avg_unrl is not None else "—")
        _kk[3].metric("Đã đóng",       _pt_stats_data["total_closed"])
        _win_rate = _pt_stats_data.get("win_rate")
        _kk[4].metric("Win Rate",      f"{_win_rate:.1f}%" if _win_rate is not None else "—",
                       help="Win = return > 0 khi đóng")
        _avg_ret = _pt_stats_data.get("avg_return")
        _kk[5].metric("Avg Return (đã đóng)", f"{_avg_ret:+.2f}%" if _avg_ret is not None else "—")

        # ── So sánh A* vs A (chỉ hiện khi có đủ data) ───────────────────────
        _star_stats    = _pt_stats_data.get("star", {})
        _nonstar_stats = _pt_stats_data.get("non_star", {})
        if _star_stats.get("n", 0) >= 3 and _nonstar_stats.get("n", 0) >= 3:
            st.markdown("**⭐ BUY-A\\* vs BUY-A — so sánh hiệu suất (đã đóng)**")
            _cmp1, _cmp2, _cmp3, _cmp4 = st.columns(4)
            _cmp1.metric("⭐ Win Rate A*",  f"{_star_stats['win_rate']:.1f}%",
                         delta=f"{_star_stats['win_rate']-(_nonstar_stats['win_rate'] or 0):+.1f}% vs A")
            _cmp2.metric("⭐ Avg Return A*", f"{_star_stats['avg_return']:+.2f}%",
                         delta=f"{_star_stats['avg_return']-(_nonstar_stats['avg_return'] or 0):+.2f}% vs A")
            _cmp3.metric("A Win Rate",  f"{_nonstar_stats['win_rate']:.1f}%")
            _cmp4.metric("A Avg Return", f"{_nonstar_stats['avg_return']:+.2f}%")

        # ── Bảng trades đang mở ──────────────────────────────────────────────
        if _pt_open:
            st.markdown("#### 📂 Danh sách đang theo dõi")

            _fc1, _fc2 = st.columns([2, 3])
            _filter_sig = _fc1.selectbox(
                "Lọc tín hiệu", ["Tất cả", "⭐ BUY-A* only", "BUY-A only"],
                key="pt_filter_sig",
            )
            _sort_by = _fc2.selectbox(
                "Sắp xếp theo", ["Return% ↑↓", "Ngày vào", "Mã CK", "Score"],
                key="pt_sort",
            )

            # Đánh số lần xuất hiện theo thứ tự entry_date, chỉ tính Return% từ lần đầu tiên
            from datetime import datetime as _dtt2
            _pt_open_sorted_by_date = sorted(_pt_open, key=lambda t: t["entry_date"])
            _sym_occurrence: dict = {}  # symbol → danh sách trade đã gặp theo thứ tự date

            _pt_open_rows = []
            for _t in _pt_open_sorted_by_date:
                _sym = _t["symbol"]
                _cur  = _t.get("current_price") or _t["entry_price"]
                _unrl = _t.get("unrealized_pct")
                try:
                    _tgt    = _dtt2.strptime(_t["t5_target"], "%Y-%m-%d")
                    _dleft  = (_tgt - _dtt2.now()).days
                    _dleft_str = f"{_dleft}d" if _dleft > 0 else "⏰ Đến hạn"
                except Exception:
                    _dleft_str = "—"

                _occ = _sym_occurrence.get(_sym, 0) + 1
                _sym_occurrence[_sym] = _occ
                _is_first = (_occ == 1)

                _sig_raw  = _t.get("signal", "BUY-A")
                _sig_disp = "⭐ BUY-A*" if (_t.get("is_star") or _sig_raw == "BUY-A*") else _sig_raw
                _pt_open_rows.append({
                    "Mã":          _sym,
                    "Tín hiệu":    _sig_disp,
                    "Ngành":       _t.get("sector", ""),
                    "Lần":         _occ,
                    "Ngày vào":    _t["entry_date"],
                    "Giá vào":     _t["entry_price"],
                    "Giá hiện":    _cur,
                    "Return%":     (round(_unrl, 2) if _unrl is not None else 0.0) if _is_first else None,
                    "T5 deadline": _t["t5_target"],
                    "Còn lại":     _dleft_str,
                    "RSI":         _t.get("rsi", "—"),
                    "Score":       _t.get("tech_score", "—"),
                    "_id":         _t["id"],
                    "_first":      _is_first,
                })

            _df_open = pd.DataFrame(_pt_open_rows)

            # Áp dụng filter tín hiệu
            if _filter_sig == "⭐ BUY-A* only":
                _df_open = _df_open[_df_open["Tín hiệu"] == "⭐ BUY-A*"]
            elif _filter_sig == "BUY-A only":
                _df_open = _df_open[_df_open["Tín hiệu"] == "BUY-A"]

            _sort_col_map = {
                "Return% ↑↓": ("Return%", False),
                "Ngày vào":   ("Ngày vào", True),
                "Mã CK":      ("Mã", True),
                "Score":      ("Score", False),
            }
            _sc, _sasc = _sort_col_map.get(_sort_by, ("Return%", False))
            _df_open = _df_open.sort_values(_sc, ascending=_sasc, na_position="last")

            st.dataframe(
                _df_open.drop(columns=["_id", "_first"]),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Tín hiệu": st.column_config.TextColumn(
                        "Tín hiệu", help="⭐ BUY-A* = đủ điều kiện alpha cao (RSI≥60 + ngành top)"),
                    "Ngành":    st.column_config.TextColumn("Ngành", width="small"),
                    "Lần":      st.column_config.NumberColumn(
                        "Lần mua", format="%d",
                        help="Lần thứ mấy mua mã này. Return% chỉ tính từ lần đầu tiên."),
                    "Return%":  st.column_config.NumberColumn(
                        "Return% (lần 1)", format="%.2f%%",
                        help="Lãi/lỗ chưa thực hiện — chỉ hiển thị cho lần mua đầu tiên"),
                    "Giá vào":  st.column_config.NumberColumn(format="%.2f"),
                    "Giá hiện": st.column_config.NumberColumn(format="%.2f"),
                    "Score":    st.column_config.NumberColumn(format="%.1f"),
                    "RSI":      st.column_config.NumberColumn(format="%.1f"),
                },
            )

            # ── Bar chart Return% theo mã — chỉ dùng lần đầu tiên ────────────
            _chart_df = _df_open[_df_open["_first"] == True].copy()
            _chart_df = _chart_df.sort_values("Return%")
            # A* = vàng khi lãi / cam khi lỗ; A thường = xanh/đỏ
            def _bar_color(row):
                is_star = row["Tín hiệu"] == "⭐ BUY-A*"
                v = row["Return%"]
                if v is None: return "#888"
                if is_star:   return "#ffd600" if v >= 0 else "#ff6d00"
                return "#00c853" if v >= 0 else "#ff1744"
            _colors = [_bar_color(r) for _, r in _chart_df.iterrows()]
            _fig_bar = _go_pt.Figure(_go_pt.Bar(
                x=_chart_df["Mã"], y=_chart_df["Return%"],
                marker_color=_colors,
                text=[f"⭐{v:+.2f}%" if (v is not None and row["Tín hiệu"] == "⭐ BUY-A*")
                      else (f"{v:+.2f}%" if v is not None else "—")
                      for v, (_, row) in zip(_chart_df["Return%"], _chart_df.iterrows())],
                textposition="outside",
            ))
            _fig_bar.update_layout(
                title="Return% từng mã (đang mở)",
                height=300, template="plotly_dark",
                margin=dict(l=0, r=0, t=40, b=0),
                yaxis=dict(ticksuffix="%", zeroline=True, zerolinecolor="#555"),
                xaxis=dict(tickangle=-45),
            )
            st.plotly_chart(_fig_bar, use_container_width=True)

            # ── Đóng trade thủ công ───────────────────────────────────────────
            with st.expander("✏️ Đóng trade thủ công"):
                _close_opts = {t["id"]: f"{t['symbol']} — vào {t['entry_date']} @ {t['entry_price']}"
                               for t in _pt_open}
                _close_id    = st.selectbox("Chọn trade", list(_close_opts.keys()),
                                            format_func=lambda x: _close_opts.get(x, x),
                                            key="pt_close_sel")
                _close_price = st.number_input("Giá thoát", min_value=0.0, step=0.1, key="pt_close_px")
                if st.button("Đóng trade này", key="pt_close_btn") and _close_id and _close_price > 0:
                    _pt_close(_close_id, _close_price)
                    st.success(f"Đã đóng {_close_id} @ {_close_price:.2f}")
                    st.rerun()
        else:
            st.info("Chưa có trade đang mở. Nhấn **📌 Ghi BUY-A hôm nay** trong tab Quick Scan để bắt đầu.")

        # ── Lịch sử đã đóng ──────────────────────────────────────────────────
        if _pt_closed:
            st.markdown("#### 📜 Lịch sử đã đóng")
            _pt_hist_rows = []
            for _t in sorted(_pt_closed, key=lambda x: x.get("exit_date", ""), reverse=True):
                _icon = "✅" if _t["result"] == "win" else "❌"
                _pt_hist_rows.append({
                    "":         _icon,
                    "Mã":       _t["symbol"],
                    "Vào":      _t["entry_date"],
                    "Ra":       _t.get("exit_date", "—"),
                    "Giá vào":  _t["entry_price"],
                    "Giá ra":   _t.get("exit_price", "—"),
                    "Return%":  _t["return_pct"] if _t.get("return_pct") is not None else 0.0,
                })
            st.dataframe(
                pd.DataFrame(_pt_hist_rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Return%": st.column_config.NumberColumn(format="%.2f%%"),
                    "Giá vào": st.column_config.NumberColumn(format="%.2f"),
                    "Giá ra":  st.column_config.NumberColumn(format="%.2f"),
                },
            )

            # Win rate tích lũy
            if len(_pt_closed) >= 3:
                _sorted_closed = sorted(_pt_closed, key=lambda x: x.get("exit_date", ""))
                _running_wins  = 0
                _wr_dates, _wr_vals = [], []
                for _i, _t in enumerate(_sorted_closed, 1):
                    if _t["result"] == "win":
                        _running_wins += 1
                    _wr_dates.append(_t.get("exit_date", ""))
                    _wr_vals.append(round(_running_wins / _i * 100, 1))
                _fig_wr = _go_pt.Figure(_go_pt.Scatter(
                    x=_wr_dates, y=_wr_vals, mode="lines+markers",
                    line=dict(color="#00e676", width=2), name="Win Rate %",
                ))
                _fig_wr.add_hline(y=50, line_dash="dash", line_color="#888",
                                   annotation_text="50%")
                _fig_wr.update_layout(
                    title="Win Rate tích lũy",
                    height=220, template="plotly_dark",
                    margin=dict(t=40, b=20, l=20, r=20),
                )
                st.plotly_chart(_fig_wr, use_container_width=True)

        # ── Xóa trade ────────────────────────────────────────────────────────
        with st.expander("🗑️ Xóa trade"):
            if _pt_trades:
                _del_opts = {t["id"]: f"{t['symbol']} ({t['status']}) — {t['entry_date']}"
                             for t in _pt_trades}
                _del_id = st.selectbox("Trade cần xóa", list(_del_opts.keys()),
                                        format_func=lambda x: _del_opts.get(x, x),
                                        key="pt_del_sel2")
                if st.button("Xóa", type="secondary", key="pt_del_btn2") and _del_id:
                    _pt_delete(_del_id)
                    st.success(f"Đã xóa {_del_id}")
                    st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    #  SUB-TAB: DANH MỤC THỰC
    # ══════════════════════════════════════════════════════════════════════════
    with _subtab_port:
        _port_mode = st.radio(
            "Nguồn dữ liệu danh mục",
            ["✏️ Nhập trực tiếp", "📁 Upload CSV", "📋 File mẫu"],
            horizontal=True,
        )

        df_port = pd.DataFrame()

        if _port_mode == "✏️ Nhập trực tiếp":
            st.caption("Nhập hoặc chỉnh sửa trực tiếp. Dữ liệu tự lưu khi nhấn **Lưu danh mục**.")
            _dm_loaded = load_portfolio_manual()

            _edited = st.data_editor(
                _dm_loaded,
                use_container_width=True,
                num_rows="dynamic",
                column_config={
                    "symbol":    st.column_config.TextColumn("Mã CK", max_chars=10,
                                    help="Ví dụ: HPG, VNM, ACB"),
                    "quantity":  st.column_config.NumberColumn("Số lượng (CP)", min_value=0, step=100,
                                    format="%d"),
                    "avg_price": st.column_config.NumberColumn("Giá vốn TB (VNĐ)", min_value=0,
                                    format="%.0f"),
                    "sector":    st.column_config.TextColumn(
                                    "Ngành", width="medium",
                                    help="Nhập tay hoặc dùng nút '🏭 Tự điền ngành' để tự động điền từ vnstock",
                                 ),
                },
                hide_index=True,
                key="port_editor",
            )

            _pc1, _pc2 = st.columns([1, 1])
            if _pc1.button("💾 Lưu danh mục", type="primary", use_container_width=True):
                _e = _edited.copy()
                _e["symbol"]   = _e["symbol"].astype(str).str.strip()
                _e["quantity"] = pd.to_numeric(_e["quantity"], errors="coerce").fillna(0)
                _to_save = _e[_e["symbol"].ne("") & (_e["quantity"] > 0)]
                save_portfolio_manual(_to_save)
                st.success(f"Đã lưu {len(_to_save)} mã.")
                st.rerun()
            if _pc2.button("🏭 Tự điền ngành", use_container_width=True,
                           help="Truy vấn ngành từ vnstock cho các mã chưa có hoặc 'Chưa phân loại'"):
                _e2 = _edited.copy()
                _e2["symbol"] = _e2["symbol"].astype(str).str.strip().str.upper()
                _NO_SECTOR = {"", "none", "nan", "chưa phân loại", "chua phan loai"}
                def _missing_sector(v):
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        return True
                    return str(v).strip().lower() in _NO_SECTOR
                _need = _e2[
                    _e2["symbol"].ne("") & _e2["sector"].apply(_missing_sector)
                ]["symbol"].tolist()
                if _need:
                    with st.spinner(f"Đang truy vấn ngành cho {len(_need)} mã..."):
                        _sec_map = fetch_sector_batch(_need)
                    _e2.loc[_e2["symbol"].isin(_need), "sector"] = _e2.loc[
                        _e2["symbol"].isin(_need), "symbol"
                    ].map(_sec_map)
                    _to_save2 = _e2[_e2["symbol"].ne("") & (pd.to_numeric(_e2["quantity"], errors="coerce").fillna(0) > 0)]
                    save_portfolio_manual(_to_save2)
                    st.success(f"Đã điền ngành cho: {', '.join(_need)}")
                    st.rerun()
                else:
                    st.info("Tất cả mã đã có ngành.")

            # Nút xóa mã
            _saved_syms = _dm_loaded[
                _dm_loaded["symbol"].astype(str).str.strip().ne("") &
                (_dm_loaded["quantity"] > 0)
            ]["symbol"].tolist()
            if _saved_syms:
                _del_col1, _del_col2 = st.columns([1, 1])
                _del_sym = _del_col1.selectbox("Chọn mã cần xóa", ["—"] + _saved_syms,
                                               key="del_sym_select")
                if _del_col2.button("🗑️ Xóa mã đã chọn", use_container_width=True,
                                    disabled=(_del_sym == "—")):
                    _kept = _dm_loaded[_dm_loaded["symbol"] != _del_sym]
                    save_portfolio_manual(_kept)
                    st.success(f"Đã xóa {_del_sym} khỏi danh mục.")
                    st.rerun()

            df_port = load_portfolio_manual()
            df_port = df_port[
                df_port["symbol"].astype(str).str.strip().ne("") &
                (df_port["quantity"] > 0)
            ]

        elif _port_mode == "📁 Upload CSV":
            uploaded = st.file_uploader("Upload file CSV danh mục", type="csv",
                help="Cột bắt buộc: symbol, quantity, avg_price. Tùy chọn: sector")
            if uploaded:
                df_port = load_portfolio(io.StringIO(uploaded.read().decode("utf-8-sig")))
            else:
                st.markdown("**Định dạng CSV:**\n```\nsymbol,quantity,avg_price,sector\nHPG,1000,25000,Thép\n```")

        else:  # File mẫu
            if Path("portfolio_mau.csv").exists():
                df_port = load_portfolio("portfolio_mau.csv")
            else:
                st.warning("Không tìm thấy portfolio_mau.csv")

        # ── Hiển thị kết quả P&L ─────────────────────────────────────────────
        if not df_port.empty:
            with st.spinner("Đang lấy giá hiện tại..."):
                df_enriched = enrich_portfolio(df_port, source=source)
            summary = portfolio_summary(df_enriched)

            st.divider()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Số cổ phiếu",        summary.get("num_stocks", 0))
            k2.metric("Giá vốn",            f"{summary.get('total_cost',0):,.0f} VNĐ")
            k3.metric("Giá trị thị trường", f"{summary.get('total_value',0):,.0f} VNĐ")
            pnl     = summary.get("total_pnl", 0)
            pnl_pct = summary.get("total_pnl_pct", 0)
            k4.metric("Lãi/Lỗ", f"{pnl:+,.0f} VNĐ", delta=f"{pnl_pct:+.2f}%", delta_color="normal")

            st.divider()
            st.subheader("Chi tiết danh mục")

            _port_syms = df_port["symbol"].tolist()
            _psig_btn_col, _psig_info_col = st.columns([1, 3])
            if _psig_btn_col.button("🔄 Làm mới tín hiệu", use_container_width=True,
                                    help="Tính lại tín hiệu real-time từ vnstock + Amibroker cho từng mã trong danh mục"):
                from concurrent.futures import ThreadPoolExecutor, as_completed
                from vn_invest.screener import get_ami_scan_data as _get_ami_data
                from vn_invest.data import get_price_history as _get_hist
                from vn_invest.indicators import add_all_indicators as _add_ind, get_latest_signals as _get_sig
                _ami_all = _get_ami_data()
                _port_sig_map = {}
                _pb = st.progress(0, text="Đang tải dữ liệu...")
                _done = [0]

                def _scan_one(sym):
                    try:
                        _df = _get_hist(sym, days=120, source=source)
                        if _df is None or len(_df) < 35:
                            return sym, None
                        _df = _add_ind(_df)
                        _wmt = _ami_all.get(sym, {}).get("ami_wmt")
                        if _wmt is not None:
                            _df["weekly_macd_trend"] = int(_wmt)
                        return sym, _get_sig(_df)
                    except Exception:
                        return sym, None

                with ThreadPoolExecutor(max_workers=4) as _ex:
                    _futs = {_ex.submit(_scan_one, s): s for s in _port_syms}
                    for _f in as_completed(_futs):
                        _s, _res = _f.result()
                        if _res:
                            _port_sig_map[_s] = _res
                        _done[0] += 1
                        _pb.progress(_done[0] / len(_port_syms),
                                     text=f"Đã xử lý {_done[0]}/{len(_port_syms)} mã...")
                _pb.empty()
                st.session_state["portfolio_signals"] = _port_sig_map
                _psig_info_col.success(f"✅ Đã cập nhật tín hiệu real-time cho {len(_port_sig_map)}/{len(_port_syms)} mã")

            _port_sig_map = st.session_state.get("portfolio_signals", {})
            if _port_sig_map:
                _psig_info_col.caption(f"Tín hiệu real-time · {len(_port_sig_map)} mã")
            else:
                _psig_info_col.caption("Tín hiệu từ cache scan · Nhấn 'Làm mới tín hiệu' để tính real-time")

            if "pnl" in df_enriched.columns:
                _cache_map = {r["symbol"]: r for r in (st.session_state.get("scan_cache") or load_cache())}
                _SIG_COLOR = {"BUY-A":"#00e676","BUY-B":"#69f0ae","HOLD":"#ffd740",
                              "SELL-B":"#ff9800","SELL-A":"#ff5252"}

                def _pnl_color(pct):
                    if pd.isna(pct): return ""
                    if pct >= 10:  return "background:rgba(0,230,118,0.18)"
                    if pct >= 3:   return "background:rgba(0,230,118,0.09)"
                    if pct <= -10: return "background:rgba(255,82,82,0.18)"
                    if pct <= -3:  return "background:rgba(255,82,82,0.09)"
                    return ""

                _port_rows = []
                for _, r in df_enriched.iterrows():
                    _bg   = _pnl_color(r.get("pnl_pct"))
                    _sym  = r.get("symbol", "")
                    _cur  = r.get("current_price")
                    _avg  = r.get("avg_price", 0)
                    _qty  = r.get("quantity", 0)
                    _mv   = r.get("market_value")
                    _pl   = r.get("pnl")
                    _pp   = r.get("pnl_pct")
                    _chg  = r.get("session_change_pct")
                    _sec  = r.get("sector", "")

                    _rt_sig = _port_sig_map.get(_sym)
                    _cached = _cache_map.get(_sym, {})
                    _sig = (_rt_sig.get("signal", "") if _rt_sig else None) or \
                           _cached.get("signal") or _cached.get("signal_class", "")
                    _sigc = _SIG_COLOR.get(_sig, "#aaa")
                    _sig_s = (f'<span style="color:{_sigc};font-weight:600">{_sig}</span>'
                              if _sig else "—")

                    _cur_s = f"{_cur:,.0f}" if _cur and not pd.isna(_cur) else "—"
                    _mv_s  = f"{_mv:,.0f}"  if _mv  and not pd.isna(_mv)  else "—"
                    _pl_s  = (f'<span style="color:{"#00e676" if _pl>=0 else "#ff5252"}">'
                              f'{_pl:+,.0f}</span>') if _pl is not None and not pd.isna(_pl) else "—"
                    _pp_s  = (f'<span style="color:{"#00e676" if _pp>=0 else "#ff5252"}">'
                              f'{_pp:+.2f}%</span>') if _pp is not None and not pd.isna(_pp) else "—"
                    _chg_s = (f'<span style="color:{"#00e676" if _chg>=0 else "#ff5252"}">'
                              f'{_chg:+.2f}%</span>') if _chg is not None and not pd.isna(_chg) else "—"

                    _cpats = [p for p in [
                        _cached.get("candle_patterns",""), _cached.get("chart_patterns","")
                    ] if p]
                    _pat_s = " | ".join(_cpats) if _cpats else "—"
                    if len(_pat_s) > 60:
                        _pat_s = _pat_s[:57] + "..."

                    _port_rows.append(
                        f'<tr style="{_bg}">'
                        f'<td style="padding:5px 8px;font-weight:600">{_sym}</td>'
                        f'<td style="padding:5px 8px;text-align:right">{_qty:,}</td>'
                        f'<td style="padding:5px 8px;text-align:right">{_avg:,.0f}</td>'
                        f'<td style="padding:5px 8px;text-align:right">{_cur_s}</td>'
                        f'<td style="padding:5px 8px;text-align:right">{_chg_s}</td>'
                        f'<td style="padding:5px 8px;text-align:right">{_mv_s}</td>'
                        f'<td style="padding:5px 8px;text-align:right">{_pl_s}</td>'
                        f'<td style="padding:5px 8px;text-align:right">{_pp_s}</td>'
                        f'<td style="padding:5px 8px;text-align:center">{_sig_s}</td>'
                        f'<td style="padding:5px 8px;color:#aaa;font-size:0.82em">{_pat_s}</td>'
                        f'<td style="padding:5px 8px;color:#888">{_sec}</td>'
                        f'</tr>'
                    )
                _port_table = (
                    '<table style="width:100%;border-collapse:collapse;font-size:0.85em">'
                    '<thead><tr style="border-bottom:1px solid #333;color:#aaa;font-size:0.9em">'
                    '<th style="padding:5px 8px;text-align:left">Mã</th>'
                    '<th style="padding:5px 8px;text-align:right">Số lượng</th>'
                    '<th style="padding:5px 8px;text-align:right">Giá vốn</th>'
                    '<th style="padding:5px 8px;text-align:right">Giá HT</th>'
                    '<th style="padding:5px 8px;text-align:right">%Phiên</th>'
                    '<th style="padding:5px 8px;text-align:right">GT TT</th>'
                    '<th style="padding:5px 8px;text-align:right">Lãi/Lỗ (₫)</th>'
                    '<th style="padding:5px 8px;text-align:right">L/L%</th>'
                    '<th style="padding:5px 8px;text-align:center">Tín hiệu</th>'
                    '<th style="padding:5px 8px;text-align:left">Mô hình giá</th>'
                    '<th style="padding:5px 8px;text-align:left">Ngành</th>'
                    '</tr></thead><tbody>'
                    + "".join(_port_rows)
                    + '</tbody></table>'
                )
                st.markdown(_port_table, unsafe_allow_html=True)

            st.divider()
            st.subheader("Phân bổ theo ngành")
            df_sector = sector_allocation(df_enriched)
            if not df_sector.empty:
                col_chart, col_table = st.columns([2, 1])
                with col_chart:
                    st.bar_chart(df_sector.set_index("sector")["weight_pct"], use_container_width=True)
                with col_table:
                    st.dataframe(
                        df_sector.rename(columns={"sector":"Ngành","market_value":"GT (VNĐ)","weight_pct":"Tỷ trọng (%)"}),
                        use_container_width=True, hide_index=True,
                        column_config={
                            "GT (VNĐ)":     st.column_config.NumberColumn(format="%,.0f"),
                            "Tỷ trọng (%)": st.column_config.NumberColumn(format="%.1f%%"),
                        })
