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

    st.header("Danh mục đầu tư")

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

    # ── Hiển thị kết quả P&L ─────────────────────────────────────────────────
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

    # ── Paper Trading Tracker ─────────────────────────────────────────────────
    st.divider()
    st.subheader("📌 Paper Trading — Theo dõi BUY-A T+5 tuần")

    from vn_invest.paper_trading import (
        load_trades as _pt_load, close_trade as _pt_close,
        delete_trade as _pt_delete, update_prices as _pt_update,
        get_stats as _pt_stats,
    )

    _pt_trades = _pt_load()

    _pt_price_map: dict = {}
    _pt_cache_src = st.session_state.get("scan_cache") or load_cache()
    if _pt_cache_src:
        _pt_price_map = {r["symbol"]: float(r["close"]) for r in _pt_cache_src
                         if r.get("symbol") and r.get("close")}
    if _pt_price_map and _pt_trades:
        _pt_trades = _pt_update(_pt_price_map)

    _pt_stats_data = _pt_stats(_pt_trades)
    _pt_open  = [t for t in _pt_trades if t["status"] == "open"]
    _pt_closed = [t for t in _pt_trades if t["status"] == "closed"]

    _ks = st.columns(5)
    _ks[0].metric("Đang mở", _pt_stats_data["total_open"])
    _ks[1].metric("Đã đóng", _pt_stats_data["total_closed"])
    _win_rate = _pt_stats_data.get("win_rate")
    _ks[2].metric("Win Rate", f"{_win_rate:.1f}%" if _win_rate is not None else "—",
                  help="Win = return > 0 sau T+5 tuần")
    _avg_ret = _pt_stats_data.get("avg_return")
    _ks[3].metric("Avg Return", f"{_avg_ret:+.2f}%" if _avg_ret is not None else "—")
    _best = _pt_stats_data.get("best")
    _ks[4].metric("Best / Worst",
                  f"{_best:+.1f}% / {_pt_stats_data.get('worst', 0):+.1f}%" if _best is not None else "—")

    if _pt_open:
        st.markdown("**📂 Đang theo dõi**")
        _pt_open_rows = []
        for _t in _pt_open:
            _cur  = _t.get("current_price") or _t["entry_price"]
            _unrl = _t.get("unrealized_pct")
            _days_left = ""
            try:
                from datetime import datetime as _dtt
                _tgt  = _dtt.strptime(_t["t5_target"], "%Y-%m-%d")
                _dleft = (_tgt - _dtt.now()).days
                _days_left = f"{_dleft}d" if _dleft >= 0 else "⏰ Đến hạn"
            except Exception:
                pass
            _pt_open_rows.append({
                "Mã":        _t["symbol"],
                "Ngày vào":  _t["entry_date"],
                "Giá vào":   _t["entry_price"],
                "Giá hiện":  _cur,
                "Return%":   f"{_unrl:+.2f}%" if _unrl is not None else "—",
                "T5 deadline": _t["t5_target"],
                "Còn lại":   _days_left,
                "RSI":       _t.get("rsi", "—"),
                "Score":     _t.get("tech_score", "—"),
                "_id":       _t["id"],
            })

        _df_open = pd.DataFrame(_pt_open_rows)
        st.dataframe(
            _df_open.drop(columns=["_id"]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Return%": st.column_config.TextColumn("Return%", width="small"),
                "Giá vào": st.column_config.NumberColumn(format="%.2f"),
                "Giá hiện": st.column_config.NumberColumn(format="%.2f"),
            }
        )

        with st.expander("✏️ Đóng trade thủ công"):
            _close_id   = st.selectbox("Chọn trade", [t["id"] for t in _pt_open],
                                        format_func=lambda x: x)
            _close_price = st.number_input("Giá thoát", min_value=0.0, step=0.1)
            if st.button("Đóng trade này") and _close_id and _close_price > 0:
                _pt_close(_close_id, _close_price)
                st.success(f"Đã đóng {_close_id} @ {_close_price}")
                st.rerun()
    else:
        st.info("Chưa có trade đang mở. Nhấn **📌 Ghi BUY-A hôm nay** trong tab Quick Scan để bắt đầu.")

    if _pt_closed:
        st.markdown("**📜 Lịch sử đã đóng**")
        _pt_hist_rows = []
        for _t in sorted(_pt_closed, key=lambda x: x.get("exit_date",""), reverse=True):
            _icon = "✅" if _t["result"] == "win" else "❌"
            _pt_hist_rows.append({
                "":          _icon,
                "Mã":        _t["symbol"],
                "Vào":       _t["entry_date"],
                "Ra":        _t.get("exit_date","—"),
                "Giá vào":   _t["entry_price"],
                "Giá ra":    _t.get("exit_price","—"),
                "Return%":   f"{_t['return_pct']:+.2f}%" if _t.get("return_pct") is not None else "—",
            })
        st.dataframe(pd.DataFrame(_pt_hist_rows), use_container_width=True, hide_index=True)

        if len(_pt_closed) >= 3:
            import plotly.graph_objects as _go_pt
            _sorted_closed = sorted(_pt_closed, key=lambda x: x.get("exit_date",""))
            _running_wins  = 0
            _wr_dates, _wr_vals = [], []
            for _i, _t in enumerate(_sorted_closed, 1):
                if _t["result"] == "win": _running_wins += 1
                _wr_dates.append(_t.get("exit_date",""))
                _wr_vals.append(round(_running_wins / _i * 100, 1))
            _fig_wr = _go_pt.Figure()
            _fig_wr.add_trace(_go_pt.Scatter(x=_wr_dates, y=_wr_vals, mode="lines+markers",
                                              line=dict(color="#00e676", width=2),
                                              name="Win Rate %"))
            _fig_wr.add_hline(y=50, line_dash="dash", line_color="#888",
                               annotation_text="50%")
            _fig_wr.update_layout(title="Win Rate tích lũy theo thời gian",
                                   height=250, margin=dict(t=40,b=20,l=20,r=20),
                                   paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                                   font=dict(color="#ddd"))
            st.plotly_chart(_fig_wr, use_container_width=True)

    with st.expander("🗑️ Xóa trade"):
        _del_id = st.selectbox("Trade cần xóa", [t["id"] for t in _pt_trades],
                                format_func=lambda x: x, key="pt_del_sel")
        if st.button("Xóa", type="secondary") and _del_id:
            _pt_delete(_del_id)
            st.success(f"Đã xóa {_del_id}")
            st.rerun()
