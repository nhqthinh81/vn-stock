"""TAB 1 — CƠ BẢN: Phân tích cơ bản cổ phiếu."""
import os
import subprocess
import urllib.parse

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from vn_invest.investing import COMMON_PAIRS
from vn_invest.screener import load_cache
from tabs.chatbot_helpers import _build_basic_context, _render_chatbot


def render(ctx: dict) -> None:
    symbol_input = ctx["symbol_input"]
    source = ctx["source"]
    _fetch_overview = ctx["fetch_overview"]
    _fetch_ratio_hist = ctx["fetch_ratio_hist"]
    _fetch_stock_status = ctx["fetch_stock_status"]
    _fetch_statements = ctx["fetch_statements"]
    _fetch_shareholders = ctx["fetch_shareholders"]
    _fetch_news = ctx["fetch_news"]
    _fetch_events = ctx["fetch_events"]
    _fetch_dividends = ctx["fetch_dividends"]
    _fetch_capital_history = ctx["fetch_capital_history"]
    _fetch_market_breadth = ctx["fetch_market_breadth"]
    _fetch_macro = ctx["fetch_macro"]

    # ── Độ rộng thị trường (3 sàn) ───────────────────────────────────────────
    _mb_cache_key = id(st.session_state.get("scan_cache"))
    if (st.session_state.get("_breadth_cache_key") != _mb_cache_key
            or not st.session_state.get("_breadth_cache")):
        _mb_syms_fb = st.session_state.get("scan_cache") or load_cache()
        if _mb_syms_fb:
            _mb_sym_list = [r["symbol"] for r in _mb_syms_fb if r.get("symbol")]
            if _mb_sym_list:
                _computed = _fetch_market_breadth(f"breadth_{len(_mb_sym_list)}", tuple(_mb_sym_list))
                st.session_state["_breadth_cache"]     = _computed
                st.session_state["_breadth_cache_key"] = _mb_cache_key
    _mb_breadth = st.session_state.get("_breadth_cache") or {}
    if _mb_breadth:
        _mb_cols = st.columns(3)
        _MB_EXCHANGES = [("HOSE", "HOSE"), ("HNX", "HNX"), ("UPCOM", "UPCOM")]
        for _mb_ci, (_mb_label, _mb_key) in enumerate(_MB_EXCHANGES):
            _b = _mb_breadth.get(_mb_key, {})
            if not _b or _b.get("total", 0) == 0:
                continue
            _adv = _b.get("advance", 0)
            _dec = _b.get("decline", 0)
            _unc = _b.get("unchanged", 0)
            _cei = _b.get("ceiling", 0)
            _flo = _b.get("floor", 0)
            _tot = _b.get("total", 1)
            _adv_pct = _adv / _tot * 100
            _dec_pct = _dec / _tot * 100
            _unc_pct = _unc / _tot * 100
            _title_color = "#00c853" if _adv > _dec else ("#ff1744" if _dec > _adv else "#ffd740")
            _mb_cols[_mb_ci].markdown(
                f"""<div style="background:#1a1d2e;border-radius:10px;padding:14px 16px;border:1px solid #2d3247">
  <div style="font-size:14px;font-weight:700;color:{_title_color};margin-bottom:8px">
    {_mb_label} &nbsp;<span style="font-size:11px;color:#888;font-weight:400">{_tot} mã</span>
  </div>
  <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin-bottom:10px">
    <div style="width:{_adv_pct:.1f}%;background:#00c853"></div>
    <div style="width:{_unc_pct:.1f}%;background:#555"></div>
    <div style="width:{_dec_pct:.1f}%;background:#ff1744"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:12px">
    <span style="color:#00c853">▲ {_adv}<br><span style="font-size:10px;color:#555">Tăng</span></span>
    <span style="color:#888">→ {_unc}<br><span style="font-size:10px;color:#555">Đứng</span></span>
    <span style="color:#ff1744">▼ {_dec}<br><span style="font-size:10px;color:#555">Giảm</span></span>
    {'<span style="color:#ff9800">⬆ ' + str(_cei) + '<br><span style="font-size:10px;color:#555">Trần</span></span>' if _cei else ''}
    {'<span style="color:#9c27b0">⬇ ' + str(_flo) + '<br><span style="font-size:10px;color:#555">Sàn</span></span>' if _flo else ''}
  </div>
</div>""",
                unsafe_allow_html=True,
            )
        st.markdown("<div style='margin-top:4px'></div>", unsafe_allow_html=True)
        st.divider()

    st.header(f"Phân tích cơ bản — {symbol_input}")

    # ── Selector chu kỳ ──────────────────────────────────────────────────────
    period_col, n_col, _ = st.columns([2, 2, 4])
    b_period  = period_col.radio("Chu kỳ", ["Năm", "Quý"], horizontal=True)
    b_n       = n_col.slider("Số kỳ", 4, 12, 8)
    api_period = "annual" if b_period == "Năm" else "quarter"

    if symbol_input in COMMON_PAIRS or "/" in symbol_input:
        st.info(f"ℹ️ **{symbol_input}** là tài sản vĩ mô / hàng hóa toàn cầu. Tài sản này không có báo cáo tài chính. Vui lòng chuyển sang **Tab Kỹ Thuật** để xem biểu đồ giá.")
        overview = {}
        hist = {"periods": [], "data": {}}
        stock_status = {"status": "normal", "badges": [], "alerts": [], "events": [], "stats": {}}
    else:
        with st.spinner("Đang tải dữ liệu..."):
            overview     = _fetch_overview(symbol_input, source)
            hist         = _fetch_ratio_hist(symbol_input, api_period, b_n, source)
            stock_status = _fetch_stock_status(symbol_input)

    if overview:
        full_name = overview.get("company_name") or overview.get("organ_name") or "—"
        short_name = overview.get("short_name") or overview.get("organ_short_name") or symbol_input
        st.markdown(f"### {short_name} &nbsp; <small style='color:#888;font-weight:normal'>{full_name}</small>",
                    unsafe_allow_html=True)

        ov_c1, ov_c2, ov_c3, ov_c4, ov_c5 = st.columns(5)
        ov_c1.metric("Sàn", overview.get("exchange", "—"))
        ov_c2.metric("Ngành", overview.get("industry_name") or overview.get("sector") or "—")
        ov_c3.metric("Loại hình", overview.get("company_type", "—"))

        emp = overview.get("number_of_employees")
        ov_c4.metric("Nhân viên", f"{int(emp):,}" if emp else "—")

        cap = overview.get("charter_capital")
        ov_c5.metric("Vốn điều lệ (tỷ)", f"{float(cap):,.0f}" if cap else "—")

        meta_parts = []
        if overview.get("ceo_name"):      meta_parts.append(f"**CEO:** {overview['ceo_name']}")
        if overview.get("listing_date"):  meta_parts.append(f"**Niêm yết:** {str(overview['listing_date'])[:10]}")
        if overview.get("auditor"):       meta_parts.append(f"**Kiểm toán:** {overview['auditor']}")
        if overview.get("website"):       meta_parts.append(f"[🌐 Website]({overview['website']})")
        if meta_parts:
            st.markdown(" &nbsp;·&nbsp; ".join(meta_parts), unsafe_allow_html=True)

        profile = overview.get("company_profile") or overview.get("business_model") or ""
        if profile and len(str(profile).strip()) > 20:
            with st.expander("📋 Giới thiệu công ty", expanded=False):
                st.markdown(str(profile)[:2000])

        # ── Nút tạo + xem báo cáo equity research ───────────────────────────
        st.markdown("---")
        _REPORT_DIR = os.path.join(os.path.expanduser("~"), "equity_reports")

        def _find_report(sym: str) -> tuple:
            if not os.path.isdir(_REPORT_DIR):
                return None, None
            candidates = []
            for fn in os.listdir(_REPORT_DIR):
                if fn.upper().startswith(sym.upper()) and fn.lower().endswith(".html"):
                    fp = os.path.join(_REPORT_DIR, fn)
                    candidates.append((os.path.getmtime(fp), fp))
            if not candidates:
                return None, None
            candidates.sort(reverse=True)
            latest_path = candidates[0][1]
            mtime = candidates[0][0]
            from datetime import datetime as _dt
            date_str = _dt.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M")
            return latest_path, date_str

        _rpt_path, _rpt_date = _find_report(symbol_input)

        _rpt_col1, _rpt_col2, _rpt_col3 = st.columns([3, 1, 1])
        with _rpt_col1:
            if _rpt_path:
                st.markdown(
                    f"**📊 Báo cáo đầy đủ** — Đã có báo cáo "
                    f"<span style='color:#00c853'>**{os.path.basename(_rpt_path)}**</span> "
                    f"(tạo lúc **{_rpt_date}**)",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown("**📊 Báo cáo phân tích đầy đủ** — DCF, DuPont, định giá, kỹ thuật, tin tức 30 ngày.")
        with _rpt_col2:
            if st.button("📊 Tạo Báo Cáo", key="btn_equity_report", use_container_width=True,
                         type="secondary" if _rpt_path else "primary"):
                st.session_state["show_equity_report_cmd"] = True
                st.session_state["show_equity_report_view"] = False
        with _rpt_col3:
            _view_disabled = _rpt_path is None
            if st.button("🔍 Xem Báo Cáo", key="btn_view_report", use_container_width=True,
                         type="primary", disabled=_view_disabled):
                st.session_state["show_equity_report_view"] = True
                st.session_state["show_equity_report_cmd"]  = False

        if st.session_state.get("show_equity_report_view") and _rpt_path:
            import streamlit.components.v1 as _components
            st.markdown(
                f"📄 **{os.path.basename(_rpt_path)}** &nbsp;·&nbsp; "
                f"<span style='color:#888'>Tạo lúc {_rpt_date}</span>",
                unsafe_allow_html=True,
            )
            _rpt_col_close, _ = st.columns([1, 5])
            if _rpt_col_close.button("✕ Đóng báo cáo", key="btn_close_report"):
                st.session_state["show_equity_report_view"] = False
                st.rerun()
            try:
                _html_content = open(_rpt_path, encoding="utf-8").read()
                _components.html(_html_content, height=900, scrolling=True)
            except Exception as _e:
                st.error(f"Không đọc được file báo cáo: {_e}")

        if st.session_state.get("show_equity_report_cmd"):
            _cmd = f"claude /equity-research-vn {symbol_input}"
            st.info(
                f"**Chạy lệnh sau trong terminal Claude Code:**\n\n"
                f"```\n{_cmd}\n```\n\n"
                f"Pipeline tạo `{symbol_input}_Complete_Report.html` tại `{_REPORT_DIR}` (~15-30 phút).",
                icon="ℹ️"
            )
            if st.button("🚀 Mở terminal & chạy tự động", key="btn_open_terminal"):
                try:
                    os.makedirs(_REPORT_DIR, exist_ok=True)
                    subprocess.Popen(
                        ["cmd", "/k", f"cd /d {_REPORT_DIR} && claude /equity-research-vn {symbol_input}"],
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                    st.success(f"Đã mở terminal tại `{_REPORT_DIR}`.")
                except Exception as _e:
                    st.error(f"Không thể mở terminal: {_e}")

    # ── Trạng thái giao dịch ─────────────────────────────────────────────────
    ss        = stock_status
    ss_status = ss.get("status", "normal")
    ss_badges = ss.get("badges", [])
    ss_alerts = ss.get("alerts", [])
    ss_events = ss.get("events", [])
    ss_stats  = ss.get("stats", {})

    if ss_status == "delisted":
        st.error("🚫 **CẢNH BÁO: Cổ phiếu đã bị hủy niêm yết hoặc đang giao dịch ngoài sàn (OTC)**")
    elif ss_status == "suspended":
        st.error("⏸ **CẢNH BÁO: Cổ phiếu đang bị tạm ngừng giao dịch**")
    elif ss_status in ("restricted", "warning"):
        st.warning("⚠️ **CHÚ Ý: Cổ phiếu trong diện cảnh báo hoặc bị hạn chế giao dịch**")

    badge_html = " &nbsp; ".join(
        f'<span style="background:{"#cc3333" if b["level"]=="danger" else "#aa7700" if b["level"]=="warning" else "#1a6b3c"};'
        f'color:#fff;padding:3px 10px;border-radius:12px;font-size:0.85em">{b["label"]}</span>'
        for b in ss_badges
    )
    st.markdown(badge_html, unsafe_allow_html=True)

    if ss_stats:
        s1, s2, s3, s4, s5, s6 = st.columns(6)
        price = ss_stats.get("current_price")
        s1.metric("Giá hiện tại", f"{price:,.0f}đ" if price else "—")

        h1y = ss_stats.get("high_1y"); l1y = ss_stats.get("low_1y")
        s2.metric("Cao nhất 52T", f"{h1y:,.0f}" if h1y else "—")
        s3.metric("Thấp nhất 52T", f"{l1y:,.0f}" if l1y else "—")

        avg_vol = ss_stats.get("avg_vol_1m")
        s4.metric("Avg Vol 1T", f"{avg_vol:,.0f}" if avg_vol else "—")

        rating = ss_stats.get("rating")
        target = ss_stats.get("target_price")
        upside = ss_stats.get("upside_pct")
        analyst = ss_stats.get("analyst", "")
        if rating:
            s5.metric("Khuyến nghị CTCK",
                      f"{rating}",
                      delta=f"TP {target:,.0f}" if target else None,
                      help=f"Analyst: {analyst}" if analyst else None)
        if upside is not None:
            s6.metric("Upside tiềm năng", f"{upside*100:+.1f}%" if upside < 10 else f"{upside:+.1f}%")

    if ss_events:
        with st.expander(f"📋 Sự kiện cảnh báo/hạn chế ({len(ss_events)})", expanded=True):
            for ev in ss_events:
                level = "🚫" if "hủy" in ev["title"].lower() or ev["code"] == "SUSP" else "⚠️"
                st.markdown(f"{level} **{ev['date']}** — {ev['title']} `{ev['code']}`")

    for alert in ss_alerts:
        st.caption(f"ℹ️ {alert}")

    st.divider()

    periods       = hist["periods"]
    hdata         = hist["data"]
    actual_period = hist.get("actual_period", api_period)
    actual_source = hist.get("actual_source", source)

    if periods and api_period == "annual" and len(periods) < 3:
        st.info(f"ℹ️ vnstock chỉ cung cấp ~4 quý gần nhất — chế độ Năm chỉ lấy được {len(periods)} kỳ Q4. Chọn **Quý** để xem đủ 4 kỳ.")

    RATIO_MAP = {
        "p_e":                 ("P/E",              "lần",  "định giá"),
        "p_b":                 ("P/B",              "lần",  "định giá"),
        "roe":                 ("ROE",              "%",    "sinh lời"),
        "roa":                 ("ROA",              "%",    "sinh lời"),
        "trailing_eps":        ("EPS",              "VNĐ",  "sinh lời"),
        "gross_profit_margin": ("Biên lãi gộp",    "%",    "biên lợi nhuận"),
        "net_profit_margin":   ("Biên lãi ròng",   "%",    "biên lợi nhuận"),
        "debt_to_equity":      ("Nợ/VCSH",         "lần",  "đòn bẩy"),
        "debt_to_assets":      ("Nợ/TS",           "%",    "đòn bẩy"),
        "quick_ratio":         ("Thanh toán nhanh","lần",  "thanh khoản"),
        "short_term_ratio":    ("Thanh toán ngắn", "lần",  "thanh khoản"),
        "interest_coverage":   ("Trả lãi vay",     "lần",  "thanh khoản"),
    }

    latest = {k: (v[0] if v else None) for k, v in hdata.items()}

    if not periods:
        st.info("Không có dữ liệu tài chính. Kiểm tra mã hoặc nguồn dữ liệu.")
    else:
        st.subheader(f"Chỉ số kỳ gần nhất — {periods[0]}")
        keys = [k for k in RATIO_MAP if k in hdata]
        for row_keys in [keys[i:i+4] for i in range(0, len(keys), 4)]:
            cols = st.columns(4)
            for col, key in zip(cols, row_keys):
                label, unit, _ = RATIO_MAP[key]
                val  = latest.get(key)
                val2 = hdata[key][1] if len(hdata.get(key, [])) > 1 else None
                delta_str = None
                if val is not None and val2 is not None:
                    delta_str = f"{val - val2:+.2f}"
                col.metric(label,
                           f"{val:,.2f} {unit}" if val is not None else "—",
                           delta=delta_str)

        st.divider()

        actual_n = len(hist.get("periods", []))
        st.subheader(f"Xu hướng {actual_n} kỳ ({b_period})")

        GROUPS = {
            "Định giá":        ["p_e", "p_b"],
            "Sinh lời":        ["roe", "roa"],
            "EPS":             ["trailing_eps"],
            "Biên lợi nhuận":  ["gross_profit_margin", "net_profit_margin"],
            "Đòn bẩy":         ["debt_to_equity", "debt_to_assets"],
            "Thanh khoản":     ["quick_ratio", "short_term_ratio", "interest_coverage"],
        }

        x_labels = list(reversed(periods))

        def _trend_chart(keys_in_group):
            fig = go.Figure()
            has_data = False
            for key in keys_in_group:
                if key not in hdata:
                    continue
                vals_raw = hdata[key]
                y_vals = list(reversed(vals_raw))
                label, unit, _ = RATIO_MAP[key]
                fig.add_trace(go.Scatter(
                    x=x_labels, y=y_vals,
                    mode="lines+markers",
                    name=f"{label} ({unit})",
                    connectgaps=False,
                    hovertemplate=f"<b>{label}</b>: %{{y:,.2f}} {unit}<extra></extra>",
                ))
                has_data = True
            if not has_data:
                return
            fig.update_layout(
                height=260,
                margin=dict(l=0, r=0, t=10, b=0),
                legend=dict(orientation="h", y=-0.25),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#cccccc"),
                xaxis=dict(gridcolor="#333"),
                yaxis=dict(gridcolor="#333"),
            )
            st.plotly_chart(fig, use_container_width=True)

        g_items = list(GROUPS.items())
        for i in range(0, len(g_items), 2):
            row_cols = st.columns(2)
            for col, (g_name, g_keys) in zip(row_cols, g_items[i:i+2]):
                with col:
                    st.markdown(f"**{g_name}**")
                    _trend_chart(g_keys)

        st.divider()

        with st.expander("📋 Bảng số liệu chi tiết tất cả kỳ", expanded=False):
            rows_list = []
            for key, (label, unit, grp) in RATIO_MAP.items():
                if key not in hdata:
                    continue
                row_d = {"Chỉ số": f"{label} ({unit})", "Nhóm": grp.title()}
                for p, v in zip(periods, hdata[key]):
                    row_d[p] = f"{v:,.2f}" if v is not None else "—"
                rows_list.append(row_d)
            if rows_list:
                st.dataframe(pd.DataFrame(rows_list), use_container_width=True, hide_index=True)

        st.subheader("Nhận định tự động")

        def _v_msg(label, cond_good, cond_warn, msg_good, msg_warn, msg_bad):
            if cond_good:   st.success(f"**{label}**: {msg_good}")
            elif cond_warn: st.warning(f"**{label}**: {msg_warn}")
            else:           st.error(f"**{label}**: {msg_bad}")

        def _trend(key, periods_list, data):
            vals = data.get(key, [])
            clean = [v for v in vals if v is not None]
            if len(clean) < 2:
                return ""
            chg = clean[0] - clean[1]
            pct = chg / abs(clean[1]) * 100 if clean[1] != 0 else 0
            arrow = "↑" if chg > 0 else "↓"
            return f" ({arrow}{abs(pct):.1f}% so kỳ trước)"

        pe = latest.get("p_e")
        pb = latest.get("p_b")
        eps = latest.get("trailing_eps")
        _nd_col1, _nd_col2 = st.columns(2)
        with _nd_col1:
            if pe is not None:
                _t = _trend("p_e", periods, hdata)
                _v_msg("Định giá (P/E)", pe < 15, pe < 25,
                   f"P/E = {pe:.1f}{_t} — khá rẻ",
                   f"P/E = {pe:.1f}{_t} — trung bình",
                   f"P/E = {pe:.1f}{_t} — cao, cần thận trọng")
            elif pb is not None:
                _v_msg("Định giá (P/B)", pb < 1.0, pb < 2.5,
                   f"P/B = {pb:.2f} — dưới giá trị sổ sách",
                   f"P/B = {pb:.2f} — hợp lý",
                   f"P/B = {pb:.2f} — cao")
            else:
                st.info("**Định giá**: Không có dữ liệu P/E, P/B từ nguồn hiện tại.")

            if pb is not None and pe is not None:
                _v_msg("Định giá (P/B)", pb < 1.0, pb < 2.5,
                   f"P/B = {pb:.2f} — dưới giá trị sổ sách",
                   f"P/B = {pb:.2f} — hợp lý",
                   f"P/B = {pb:.2f} — cao")

            if eps is not None:
                _t = _trend("trailing_eps", periods, hdata)
                _v_msg("EPS (trailing)", eps > 0, eps > -1000,
                   f"EPS = {eps:,.0f} VNĐ{_t} — có lãi",
                   f"EPS = {eps:,.0f} VNĐ{_t} — biên mỏng",
                   f"EPS = {eps:,.0f} VNĐ{_t} — lỗ")

        with _nd_col2:
            roe = latest.get("roe")
            roa = latest.get("roa")
            gpm = latest.get("gross_profit_margin")
            npm = latest.get("net_profit_margin")

            if roe is not None:
                _t = _trend("roe", periods, hdata)
                _v_msg("Sinh lời (ROE)", roe > 15, roe > 8,
                   f"ROE = {roe:.1f}%{_t} — tốt (>15%)",
                   f"ROE = {roe:.1f}%{_t} — trung bình (8-15%)",
                   f"ROE = {roe:.1f}%{_t} — thấp (<8%)")
            else:
                st.info("**Sinh lời (ROE)**: Không có dữ liệu.")

            if roa is not None:
                _t = _trend("roa", periods, hdata)
                _v_msg("Sinh lời (ROA)", roa > 8, roa > 3,
                   f"ROA = {roa:.1f}%{_t} — tốt",
                   f"ROA = {roa:.1f}%{_t} — trung bình",
                   f"ROA = {roa:.1f}%{_t} — thấp")

            if gpm is not None:
                _t = _trend("gross_profit_margin", periods, hdata)
                _v_msg("Biên lãi gộp", gpm > 30, gpm > 15,
                   f"Biên gộp = {gpm:.1f}%{_t} — cao, lợi thế cạnh tranh tốt",
                   f"Biên gộp = {gpm:.1f}%{_t} — trung bình",
                   f"Biên gộp = {gpm:.1f}%{_t} — thấp, áp lực chi phí")

            if npm is not None:
                _t = _trend("net_profit_margin", periods, hdata)
                _v_msg("Biên lãi ròng", npm > 15, npm > 5,
                   f"Biên ròng = {npm:.1f}%{_t} — xuất sắc",
                   f"Biên ròng = {npm:.1f}%{_t} — ổn",
                   f"Biên ròng = {npm:.1f}%{_t} — thấp")

        _nd_col3, _nd_col4 = st.columns(2)
        with _nd_col3:
            de  = latest.get("debt_to_equity")
            da  = latest.get("debt_to_assets")
            ic  = latest.get("interest_coverage")

            if de is not None:
                _t = _trend("debt_to_equity", periods, hdata)
                _v_msg("Đòn bẩy (Nợ/VCSH)", de < 1.0, de < 2.0,
                   f"Nợ/VCSH = {de:.2f}{_t} — lành mạnh",
                   f"Nợ/VCSH = {de:.2f}{_t} — chấp nhận được",
                   f"Nợ/VCSH = {de:.2f}{_t} — cao, rủi ro tài chính")
            elif da is not None:
                _t = _trend("debt_to_assets", periods, hdata)
                _v_msg("Đòn bẩy (Nợ/TS)", da < 0.4, da < 0.6,
                   f"Nợ/TS = {da:.1f}%{_t} — thấp, lành mạnh",
                   f"Nợ/TS = {da:.1f}%{_t} — trung bình",
                   f"Nợ/TS = {da:.1f}%{_t} — cao")
            else:
                st.info("**Đòn bẩy**: Không có dữ liệu Nợ/VCSH, Nợ/TS từ nguồn hiện tại.")

            if ic is not None:
                _t = _trend("interest_coverage", periods, hdata)
                _v_msg("Khả năng trả lãi", ic > 5, ic > 2,
                   f"EBIT/Lãi vay = {ic:.1f}x{_t} — rất an toàn",
                   f"EBIT/Lãi vay = {ic:.1f}x{_t} — ổn",
                   f"EBIT/Lãi vay = {ic:.1f}x{_t} — mỏng, cần theo dõi")

        with _nd_col4:
            qr  = latest.get("quick_ratio")
            str_ = latest.get("short_term_ratio")

            if qr is not None:
                _t = _trend("quick_ratio", periods, hdata)
                _v_msg("Thanh khoản nhanh", qr > 1.0, qr > 0.7,
                   f"Quick ratio = {qr:.2f}{_t} — tốt",
                   f"Quick ratio = {qr:.2f}{_t} — ổn",
                   f"Quick ratio = {qr:.2f}{_t} — yếu, rủi ro thanh khoản")
            elif str_ is not None:
                _t = _trend("short_term_ratio", periods, hdata)
                _v_msg("Thanh khoản ngắn hạn", str_ > 1.5, str_ > 1.0,
                   f"Current ratio = {str_:.2f}{_t} — tốt",
                   f"Current ratio = {str_:.2f}{_t} — ổn",
                   f"Current ratio = {str_:.2f}{_t} — yếu")
            else:
                st.info("**Thanh khoản**: Không có dữ liệu Quick ratio, Current ratio từ nguồn hiện tại.")

            if str_ is not None and qr is not None:
                _t = _trend("short_term_ratio", periods, hdata)
                _v_msg("Thanh khoản ngắn hạn", str_ > 1.5, str_ > 1.0,
                   f"Current ratio = {str_:.2f}{_t} — tốt",
                   f"Current ratio = {str_:.2f}{_t} — ổn",
                   f"Current ratio = {str_:.2f}{_t} — yếu")

    st.divider()

    # ── Báo cáo tài chính ────────────────────────────────────────────────────
    st.subheader("📑 Báo cáo tài chính")
    fs_period = st.radio("Chu kỳ BCTC", ["Quý", "Năm"], horizontal=True, key="fs_period")
    fs_api    = "quarterly" if fs_period == "Quý" else "annual"

    if st.button("📥 Tải & Phân tích BCTC", type="primary", key="btn_fs"):
        st.session_state["fs_loaded"]    = True
        st.session_state["fs_symbol"]    = symbol_input
        st.session_state["fs_api_period"] = fs_api

    _fs_loaded = (st.session_state.get("fs_loaded")
                  and st.session_state.get("fs_symbol") == symbol_input
                  and st.session_state.get("fs_api_period") == fs_api)

    fs_periods  = []
    fs_income   = {}
    fs_balance  = {}
    fs_cashflow = {}

    if _fs_loaded:
        with st.spinner("Đang tải BCTC từ vnstock..."):
            fs = _fetch_statements(symbol_input, fs_api)

        fs_periods  = fs.get("periods", [])
        fs_income   = fs.get("income", {})
        fs_balance  = fs.get("balance", {})
        fs_cashflow = fs.get("cashflow", {})

        if not fs_periods:
            st.warning("Không tải được BCTC. Thử lại hoặc đổi mã khác.")
        else:
            def _v(store, iid, idx=0):
                item = store.get(iid, {})
                vals = item.get("values", [])
                return vals[idx] if idx < len(vals) and vals[idx] is not None else None

            def _fmt(val, unit="tỷ"):
                if val is None: return "—"
                if unit == "tỷ":  return f"{val/1e9:,.1f}"
                if unit == "%":   return f"{val:.1f}%"
                return f"{val:,.1f}"

            is_tab, bs_tab, cf_tab, ana_tab, ai_tab = st.tabs([
                "📊 KQKD", "🏦 Cân đối kế toán", "💵 Lưu chuyển tiền tệ",
                "📐 Phân tích số liệu", "🤖 Phân tích AI chuyên sâu"
            ])

            def _render_table(store, periods):
                rows = []
                for iid, item in store.items():
                    row = {"Chỉ tiêu": item["label"]}
                    for p, v in zip(periods, item["values"]):
                        row[p] = f"{v/1e9:,.1f}" if v is not None else "—"
                    rows.append(row)
                if rows:
                    df_t = pd.DataFrame(rows)
                    st.dataframe(df_t, use_container_width=True, hide_index=True,
                                 column_config={"Chỉ tiêu": st.column_config.TextColumn(width="large")})

            with is_tab:
                st.caption("Đơn vị: tỷ đồng")
                _render_table(fs_income, fs_periods)

            with bs_tab:
                st.caption("Đơn vị: tỷ đồng")
                _render_table(fs_balance, fs_periods)

            with cf_tab:
                st.caption("Đơn vị: tỷ đồng")
                _render_table(fs_cashflow, fs_periods)

            with ana_tab:
                st.markdown("#### Phân tích tự động từ BCTC")

                def _growth(new, old):
                    if new is None or old is None or old == 0: return None
                    return (new - old) / abs(old) * 100

                def _safe_div(a, b):
                    if a is None or b is None or b == 0: return None
                    return a / b

                rev0   = _v(fs_income,   "isa3",  0)
                rev1   = _v(fs_income,   "isa3",  1)
                gp0    = _v(fs_income,   "isa5",  0)
                ebit0  = _v(fs_income,   "isa11", 0)
                pat0   = _v(fs_income,   "isa20", 0)
                pat1   = _v(fs_income,   "isa20", 1)
                int0   = _v(fs_income,   "isa8",  0)
                ta0    = _v(fs_balance,  "bsa1",  0)
                equity0= None
                for iid, item in fs_balance.items():
                    if "vốn chủ" in item["label"].lower() or "equity" in item["label"].lower():
                        v_val = _v(fs_balance, iid, 0)
                        if v_val and (equity0 is None or abs(v_val) > abs(equity0)):
                            equity0 = v_val
                debt0  = None
                for iid, item in fs_balance.items():
                    if "nợ phải trả" in item["label"].lower() or "total liab" in item["label"].lower():
                        v_val = _v(fs_balance, iid, 0)
                        if v_val and (debt0 is None or abs(v_val) > abs(debt0)):
                            debt0 = v_val
                cfo0   = None
                for iid, item in fs_cashflow.items():
                    if "lưu chuyển tiền thuần từ hoạt động kinh doanh" in item["label"].lower():
                        cfo0 = _v(fs_cashflow, iid, 0)
                        break

                rev_g  = _growth(rev0, rev1)
                pat_g  = _growth(pat0, pat1)
                gpm    = _safe_div(gp0, rev0)
                npm    = _safe_div(pat0, rev0)
                roe_fs = _safe_div(pat0, equity0)
                de_fs  = _safe_div(debt0, equity0)
                icr    = _safe_div(ebit0, abs(int0)) if int0 else None
                cfo_q  = _safe_div(cfo0, pat0) if pat0 else None

                a1, a2, a3, a4 = st.columns(4)
                a1.metric("Tăng trưởng DT", f"{rev_g:+.1f}%" if rev_g is not None else "—",
                          help="So với kỳ liền trước")
                a2.metric("Tăng trưởng LNST", f"{pat_g:+.1f}%" if pat_g is not None else "—")
                a3.metric("Biên lợi nhuận gộp", f"{gpm*100:.1f}%" if gpm else "—")
                a4.metric("Biên lợi nhuận ròng", f"{npm*100:.1f}%" if npm else "—")

                b1, b2, b3, b4 = st.columns(4)
                b1.metric("ROE (từ BCTC)", f"{roe_fs*100:.1f}%" if roe_fs else "—")
                b2.metric("Nợ/VCSH", f"{de_fs:.2f}x" if de_fs else "—")
                b3.metric("Khả năng trả lãi", f"{icr:.1f}x" if icr else "—")
                b4.metric("Chất lượng LN (CFO/PAT)", f"{cfo_q:.2f}x" if cfo_q else "—",
                          help=">1.0: lợi nhuận được hỗ trợ bởi dòng tiền thực")

                st.divider()
                st.markdown("#### Nhận định chi tiết")

                if rev_g is not None:
                    if rev_g > 20:   st.success(f"✅ **Doanh thu** tăng mạnh {rev_g:+.1f}% so kỳ trước — tín hiệu tích cực.")
                    elif rev_g > 0:  st.info(f"ℹ️ **Doanh thu** tăng nhẹ {rev_g:+.1f}%.")
                    elif rev_g > -10:st.warning(f"⚠️ **Doanh thu** giảm {rev_g:.1f}% — cần theo dõi.")
                    else:            st.error(f"🔴 **Doanh thu** giảm mạnh {rev_g:.1f}% — rủi ro cao.")

                if gpm is not None:
                    gp = gpm * 100
                    if gp > 30:    st.success(f"✅ **Biên gộp** {gp:.1f}% — rất tốt, có lợi thế cạnh tranh.")
                    elif gp > 15:  st.info(f"ℹ️ **Biên gộp** {gp:.1f}% — ở mức trung bình.")
                    else:          st.warning(f"⚠️ **Biên gộp** {gp:.1f}% — thấp, áp lực chi phí cao.")

                if pat_g is not None:
                    if pat_g > 30:    st.success(f"✅ **LNST** tăng trưởng mạnh {pat_g:+.1f}%.")
                    elif pat_g > 0:   st.info(f"ℹ️ **LNST** tăng {pat_g:+.1f}%.")
                    elif pat_g > -20: st.warning(f"⚠️ **LNST** giảm {pat_g:.1f}% — cần xem xét nguyên nhân.")
                    else:             st.error(f"🔴 **LNST** giảm mạnh {pat_g:.1f}%.")

                if de_fs is not None:
                    if de_fs < 0.5:  st.success(f"✅ **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — cấu trúc vốn lành mạnh.")
                    elif de_fs < 1.5:st.info(f"ℹ️ **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — chấp nhận được.")
                    elif de_fs < 3:  st.warning(f"⚠️ **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — khá cao.")
                    else:            st.error(f"🔴 **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — rất cao, rủi ro tài chính lớn.")

                if icr is not None:
                    if icr > 5:    st.success(f"✅ **Khả năng trả lãi** {icr:.1f}x — rất an toàn.")
                    elif icr > 2:  st.info(f"ℹ️ **Khả năng trả lãi** {icr:.1f}x — ổn.")
                    elif icr > 1:  st.warning(f"⚠️ **Khả năng trả lãi** {icr:.1f}x — mỏng, cần chú ý.")
                    else:          st.error(f"🔴 **Khả năng trả lãi** {icr:.1f}x < 1 — nguy hiểm, không đủ trả lãi!")

                if cfo_q is not None:
                    if cfo_q > 1.0:  st.success(f"✅ **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x — lợi nhuận có thực chất dòng tiền.")
                    elif cfo_q > 0.5:st.info(f"ℹ️ **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x — tương đối ổn.")
                    elif cfo_q >= 0: st.warning(f"⚠️ **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x — LN chưa chuyển hóa thành tiền mặt.")
                    else:            st.error(f"🔴 **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x âm — dòng tiền kinh doanh âm trong khi báo lãi.")

                if len(fs_periods) >= 3:
                    st.divider()
                    st.markdown("#### Xu hướng đa kỳ")
                    import plotly.graph_objects as _go2
                    x_rev = list(reversed(fs_periods))
                    rev_vals = list(reversed([_v(fs_income, "isa3", i) for i in range(len(fs_periods))]))
                    pat_vals = list(reversed([_v(fs_income, "isa20", i) for i in range(len(fs_periods))]))
                    gp_vals  = list(reversed([_v(fs_income, "isa5", i) for i in range(len(fs_periods))]))
                    fig_trend = _go2.Figure()
                    for name, vals, color in [
                        ("Doanh thu thuần", rev_vals, "#4da6ff"),
                        ("Lợi nhuận gộp",  gp_vals,  "#00cc88"),
                        ("LNST",           pat_vals, "#ff9944"),
                    ]:
                        y = [v/1e9 if v else None for v in vals]
                        fig_trend.add_trace(_go2.Bar(x=x_rev, y=y, name=name,
                                                     hovertemplate=f"<b>{name}</b>: %{{y:,.1f}} tỷ<extra></extra>"))
                    fig_trend.update_layout(
                        barmode="group", height=300,
                        margin=dict(l=0, r=0, t=10, b=0),
                        legend=dict(orientation="h", y=-0.3),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#cccccc"),
                        xaxis=dict(gridcolor="#333"), yaxis=dict(gridcolor="#333", title="Tỷ đồng"),
                    )
                    st.plotly_chart(fig_trend, use_container_width=True)

                # ── 1. Định giá 3 kịch bản ───────────────────────────────
                st.divider()
                st.markdown("#### 🎯 Định giá 3 kịch bản")
                pe_now  = latest.get("p_e")
                eps_now = latest.get("trailing_eps")
                if pe_now is not None and eps_now is not None and eps_now > 0:
                    g_base = pat_g / 100 if pat_g is not None else 0.08
                    g_base = max(min(g_base, 0.40), -0.30)
                    scenarios = [
                        ("🔴 Bi quan",  max(pe_now * 0.7, 3), min(g_base, 0.0) - 0.05),
                        ("🟡 Trung bình", pe_now,                g_base),
                        ("🟢 Lạc quan",  pe_now * 1.2,           max(g_base, 0.0) + 0.10),
                    ]
                    sc_cols = st.columns(3)
                    for col, (label, pe_s, g_s) in zip(sc_cols, scenarios):
                        eps_fwd = eps_now * (1 + g_s)
                        fair_value = pe_s * eps_fwd
                        col.metric(label, f"{fair_value:,.0f} VNĐ",
                                   help=f"P/E giả định {pe_s:.1f}x, tăng trưởng LNST giả định {g_s*100:+.1f}%")
                    st.caption("Cơ sở: P/E hiện tại điều chỉnh ±20-30% theo kịch bản, "
                               "EPS dự phóng = EPS hiện tại × (1 + tăng trưởng LNST kỳ gần nhất, giới hạn -30%→+40%). "
                               "Chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.")
                else:
                    st.info("Không đủ dữ liệu P/E hoặc EPS để định giá 3 kịch bản.")

                # ── 2. Dấu hiệu cảnh báo (red-flags) ─────────────────────
                st.divider()
                st.markdown("#### 🚩 Dấu hiệu cảnh báo")
                flags = []
                if rev_g is not None and pat_g is not None and rev_g > 0 and pat_g < 0:
                    flags.append("Doanh thu tăng nhưng LNST giảm — biên lợi nhuận đang xói mòn.")
                if cfo_q is not None and cfo_q < 0:
                    flags.append("Dòng tiền kinh doanh (CFO) âm trong khi vẫn báo lãi — chất lượng lợi nhuận đáng ngờ.")
                if cfo_q is not None and 0 <= cfo_q < 0.3 and pat0 is not None and pat0 > 0:
                    flags.append("LNST dương nhưng CFO/PAT rất thấp (<0.3x) — lãi chủ yếu trên sổ sách.")
                if de_fs is not None and de_fs > 3:
                    flags.append("Nợ/VCSH > 3x — đòn bẩy tài chính rất cao, rủi ro mất khả năng thanh toán.")
                if icr is not None and icr < 1:
                    flags.append("EBIT không đủ trả lãi vay (Khả năng trả lãi < 1x) — nguy cơ mất khả năng chi trả.")
                if pat_g is not None and pat_g < -50:
                    flags.append(f"LNST giảm mạnh {pat_g:.1f}% so với kỳ trước.")
                if rev_g is not None and rev_g < -30:
                    flags.append(f"Doanh thu sụt giảm mạnh {rev_g:.1f}% — dấu hiệu suy yếu hoạt động kinh doanh.")
                if gpm is not None and gpm * 100 < 5:
                    flags.append(f"Biên lợi nhuận gộp cực thấp ({gpm*100:.1f}%) — gần như không có lợi thế cạnh tranh về giá vốn.")

                if flags:
                    for f in flags:
                        st.error(f"🔴 {f}")
                    st.caption(f"Phát hiện {len(flags)}/8 dấu hiệu cảnh báo dựa trên dữ liệu BCTC kỳ gần nhất.")
                else:
                    st.success("✅ Không phát hiện dấu hiệu cảnh báo bất thường nào trong 8 tiêu chí kiểm tra từ BCTC.")

                # ── 3. Checklist đầu tư 6 bước ───────────────────────────
                st.divider()
                st.markdown("#### ✅ Checklist đầu tư")
                checklist = [
                    ("Định giá hợp lý (P/E < 25 hoặc P/B < 2.5)",
                     (pe_now is not None and pe_now < 25) or (latest.get("p_b") is not None and latest.get("p_b") < 2.5)),
                    ("ROE > 15%", (latest.get("roe") or 0) > 15),
                    ("Nợ/VCSH < 1.5x", de_fs is not None and de_fs < 1.5),
                    ("Dòng tiền kinh doanh dương (CFO > 0)", cfo0 is not None and cfo0 > 0),
                    ("LNST tăng trưởng dương so với kỳ trước", pat_g is not None and pat_g > 0),
                    ("Không có dấu hiệu cảnh báo nào ở trên", len(flags) == 0),
                ]
                n_pass = sum(1 for _, ok in checklist if ok)
                for idx, (label, ok) in enumerate(checklist):
                    st.checkbox(label, value=bool(ok), disabled=True, key=f"chk_{symbol_input}_{idx}")
                st.progress(n_pass / len(checklist),
                            text=f"Đạt {n_pass}/{len(checklist)} tiêu chí")
                if n_pass >= 5:
                    st.success("✅ Đạt phần lớn tiêu chí — đáng để nghiên cứu sâu hơn.")
                elif n_pass >= 3:
                    st.warning("⚠️ Đạt một phần tiêu chí — cần cân nhắc thêm trước khi đầu tư.")
                else:
                    st.error("🔴 Đạt ít tiêu chí — rủi ro cao, cần thận trọng.")

            with ai_tab:
                st.markdown("#### 🤖 Phân tích AI chuyên sâu")
                st.caption("Claude Haiku phân tích BCTC + bối cảnh ngành, mảng kinh doanh, rủi ro, nhận định đầu tư.")

                has_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
                if not has_key:
                    st.warning("⚠️ Chưa có `ANTHROPIC_API_KEY`. Thêm vào file `.env` rồi restart app:\n```\nANTHROPIC_API_KEY=sk-ant-...\n```")
                else:
                    ai_cache_key = f"ai_analysis_{symbol_input}_{fs_api}"
                    _btn_col, _credit_col = st.columns([2, 3])
                    with _btn_col:
                        _ai_clicked = st.button("🚀 Chạy phân tích AI", key="btn_ai_analysis", type="primary")
                    with _credit_col:
                        st.markdown(
                            "💳 **Số dư credit:** "
                            "[Xem tại Anthropic Console](https://console.anthropic.com/settings/billing) &nbsp;|&nbsp; "
                            "~$0.004/lần phân tích (Haiku 4k tokens)",
                            unsafe_allow_html=True,
                        )
                    if _ai_clicked:
                        if ai_cache_key in st.session_state:
                            del st.session_state[ai_cache_key]

                    if ai_cache_key not in st.session_state:
                        with st.spinner("Claude đang phân tích BCTC... (~15-30 giây)"):
                            from vn_invest.analyzer import analyze_bctc
                            from vn_invest.data import get_price_history
                            _co_name = (overview.get("company_name") or
                                        overview.get("organ_name") or symbol_input)
                            _sector  = (overview.get("industry_name") or
                                        overview.get("sector_vn") or
                                        overview.get("sector") or "")
                            _profile = (overview.get("company_profile") or
                                        overview.get("profile") or
                                        overview.get("business_model") or "")
                            try:
                                from vn_invest.news_fetcher import search_market_news
                                _rss_debug = search_market_news(symbol_input, _co_name, _sector, max_results=15)
                                st.session_state[f"rss_debug_{symbol_input}"] = _rss_debug
                            except Exception as _e:
                                st.session_state[f"rss_debug_{symbol_input}"] = f"Lỗi: {_e}"

                            _ai_fs = _fetch_statements(symbol_input, "quarterly")
                            _ai_periods  = _ai_fs.get("periods", [])
                            _ai_income   = _ai_fs.get("income", {})
                            _ai_balance  = _ai_fs.get("balance", {})
                            _ai_cashflow = _ai_fs.get("cashflow", {})

                            _news        = _fetch_news(symbol_input)
                            _events      = _fetch_events(symbol_input)
                            _shareholders = _fetch_shareholders(symbol_input)
                            _price_hist = []
                            try:
                                _ph = get_price_history(symbol_input, days=30)
                                if _ph is not None and not _ph.empty:
                                    for _, _r in _ph.tail(20).iterrows():
                                        _price_hist.append({
                                            "date":   str(_r.get("time", ""))[:10],
                                            "close":  _r.get("close"),
                                            "volume": _r.get("volume"),
                                        })
                            except Exception:
                                pass
                            result_text = analyze_bctc(
                                symbol=symbol_input,
                                company_name=_co_name,
                                sector=_sector,
                                profile=_profile,
                                periods=_ai_periods,
                                income=_ai_income,
                                balance=_ai_balance,
                                cashflow=_ai_cashflow,
                                recent_news=_news,
                                recent_events=_events,
                                shareholders=_shareholders,
                                price_hist=_price_hist,
                                macro=_fetch_macro(),
                            )
                            st.session_state[ai_cache_key] = result_text

                    if ai_cache_key in st.session_state:
                        st.markdown(st.session_state[ai_cache_key])
                        st.caption("_Phân tích do Claude AI tạo ra — chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._")

                        st.divider()
                        pb_cache_key = f"phan_bien_{symbol_input}_{fs_api}"
                        _pb_col1, _pb_col2 = st.columns([2, 5])
                        with _pb_col1:
                            _pb_clicked = st.button("⚖️ Phản biện phân tích", key="btn_phan_bien")
                        with _pb_col2:
                            st.caption("Tìm lỗ hổng, rủi ro bị bỏ qua và giả định chưa kiểm chứng trong phân tích trên.")
                        if _pb_clicked and pb_cache_key in st.session_state:
                            del st.session_state[pb_cache_key]
                        if _pb_clicked or pb_cache_key in st.session_state:
                            if pb_cache_key not in st.session_state:
                                with st.spinner("Đang phản biện... (~15 giây)"):
                                    _pb_prompt = f"""Bạn là chuyên gia đầu tư phản biện (devil's advocate). Nhiệm vụ: tìm lỗ hổng, rủi ro bị bỏ qua và kết luận vội vàng trong phân tích cổ phiếu dưới đây.

**Giới hạn: tối đa 600 từ. Mỗi mục 2-3 câu súc tích, có số liệu cụ thể.**

## PHÂN TÍCH CẦN PHẢN BIỆN

{st.session_state[ai_cache_key]}

---

## YÊU CẦU

Trình bày theo đúng cấu trúc này, không thêm mục nào khác:

**🔴 Rủi ro nghiêm trọng** (2-3 điểm, mỗi điểm 1-2 câu có số liệu)

**🟡 Giả định chưa kiểm chứng** (2-3 điểm, nêu cách kiểm chứng)

**🟢 Điểm có cơ sở** (1-2 điểm đáng tin cậy)

**⚖️ Verdict (3 câu):** Phân tích gốc thiên về [lạc quan/bi quan/cân bằng] vì [lý do]. Rủi ro lớn nhất bị bỏ qua là [X]. Nhà đầu tư nên [hành động cụ thể].

Trả lời tiếng Việt. Thẳng thắn, dựa trên số liệu trong phân tích gốc."""
                                    from vn_invest.analyzer import _call_claude
                                    st.session_state[pb_cache_key] = _call_claude(
                                        _pb_prompt,
                                        model="claude-sonnet-4-6",
                                        max_tokens=2048,
                                    )
                            if pb_cache_key in st.session_state:
                                st.markdown("### ⚖️ Phản biện")
                                st.markdown(st.session_state[pb_cache_key])
                                st.caption("_Phản biện do Claude AI tạo ra — mục đích giúp nhìn thấy rủi ro, không phải khuyến nghị đầu tư._")

                                st.markdown("#### 💬 Hỏi thêm về phân tích hoặc phản biện")
                                _fup_key   = f"followup_history_{symbol_input}_{fs_api}"
                                _fup_input = f"followup_input_{symbol_input}_{fs_api}"
                                if _fup_key not in st.session_state:
                                    st.session_state[_fup_key] = []

                                for _turn in st.session_state[_fup_key]:
                                    with st.chat_message("user"):
                                        st.markdown(_turn["q"])
                                    with st.chat_message("assistant"):
                                        st.markdown(_turn["a"])

                                _user_q = st.chat_input("Ví dụ: làm rõ phản biện", key=f"chat_{symbol_input}_{fs_api}")
                                if _user_q:
                                    def _fmt_raw(store, keys, label):
                                        lines = [f"### {label}"]
                                        period_hdr = " | ".join(fs_periods[:6])
                                        lines.append(f"Kỳ: {period_hdr}")
                                        for iid, v in store.items():
                                            if keys and iid not in keys:
                                                continue
                                            vals = " | ".join(
                                                f"{(x/1e9):+,.1f}ty" if x is not None else "—"
                                                for x in v["values"][:6]
                                            )
                                            lines.append(f"  {v['label'][:40]:40s}: {vals}")
                                        return "\n".join(lines)

                                    _raw_data = "\n\n".join([
                                        _fmt_raw(fs_income, {
                                            "isa1","isa3","isa4","isa5","isa9","isa10",
                                            "isa11","isa16","isa20"
                                        }, "KQKD chi tiết"),
                                        _fmt_raw(fs_balance, {
                                            "bsa2","bsa8","bsa9","bsa15","bsa16",
                                            "bsa53","bsa54","bsa55","bsa56",
                                            "bsa57","bsa67","bsa71","bsa78","bsa90"
                                        }, "CĐKT chi tiết (nợ vay + vốn)"),
                                        _fmt_raw(fs_cashflow, {
                                            "cfa9","cfa10","cfa11","cfa12",
                                            "cfa14","cfa15","cfa18",
                                            "cfa19","cfa26","cfa29","cfa30","cfa34","cfa35"
                                        }, "LCTT chi tiết (CFO/CFI/CFF)"),
                                    ])

                                    _ctx_parts = [
                                        "## Phân tích gốc\n" + st.session_state[ai_cache_key],
                                        "## Phản biện\n" + st.session_state.get(pb_cache_key, "(chưa có)"),
                                        "## Số liệu BCTC thô (tính toán chính xác)\n" + _raw_data,
                                    ]
                                    for _t in st.session_state[_fup_key][-4:]:
                                        _ctx_parts.append(f"User: {_t['q']}\nAssistant: {_t['a']}")
                                    _ctx_parts.append(f"User: {_user_q}")
                                    _fup_prompt = (
                                        "Bạn là chuyên gia phân tích và phản biện cổ phiếu. "
                                        "Dựa trên ngữ cảnh dưới đây, trả lời câu hỏi của nhà đầu tư. "
                                        "QUAN TRỌNG: Luôn hoàn thành đầy đủ câu trả lời — đặc biệt phần khuyến nghị và kết luận. "
                                        "Nếu câu hỏi đề cập đến CCC/DSO/DPO/DIO hoặc công thức tài chính, "
                                        "hãy tính toán cụ thể từ số liệu có trong ngữ cảnh.\n\n"
                                        + "\n\n---\n\n".join(_ctx_parts)
                                    )
                                    with st.spinner("Đang trả lời... (~30-60 giây với câu hỏi phức tạp)"):
                                        from vn_invest.analyzer import _call_claude
                                        _ans = _call_claude(_fup_prompt, model="claude-sonnet-4-6", max_tokens=8192)
                                    st.session_state[_fup_key].append({"q": _user_q, "a": _ans})
                                    st.rerun()

                        _rss_key = f"rss_debug_{symbol_input}"
                        if _rss_key in st.session_state:
                            _dbg = st.session_state[_rss_key]
                            if isinstance(_dbg, list):
                                with st.expander(f"🔍 Debug: RSS fetch được {len(_dbg)} bài liên quan", expanded=False):
                                    for _a in _dbg[:10]:
                                        st.caption(f"[{_a.get('lang','?')}] {_a.get('date','')[:10]} | {_a.get('source','')} | {_a.get('title','')[:80]}")
                            else:
                                st.warning(f"RSS debug: {_dbg}")

    st.divider()

    # ── Vĩ mô & Thị trường ───────────────────────────────────────────────────
    with st.expander("🌐 Vĩ mô & Thị trường", expanded=False):
        from vn_invest.macro_data import (
            fetch_global_market, fetch_vnindex_stats, load_static_macro,
            fetch_foreign_flow, fetch_wb_macro,
        )

        # ── 1. Thị trường toàn cầu (yfinance, cache 15 phút) ─────────────────
        st.markdown("#### 🌍 Thị trường toàn cầu")
        _gm = fetch_global_market()
        if _gm:
            _gm_cols = st.columns(5)
            _gm_order = ["dxy", "oil", "gold", "usdvnd", "sp500"]
            _gm_fmt = {
                "dxy":    lambda v: f"{v:.2f}",
                "oil":    lambda v: f"${v:.2f}",
                "gold":   lambda v: f"${v:,.0f}",
                "usdvnd": lambda v: f"{v:,.0f}",
                "sp500":  lambda v: f"{v:,.0f}",
            }
            for _gi, _gkey in enumerate(_gm_order):
                _gitem = _gm.get(_gkey, {})
                _gval  = _gitem.get("value")
                _gchg  = _gitem.get("chg_pct")
                _glbl  = _gitem.get("label", _gkey)
                if _gval is not None:
                    _gm_cols[_gi].metric(
                        _glbl,
                        _gm_fmt.get(_gkey, lambda v: f"{v:.2f}")(_gval),
                        f"{_gchg:+.2f}%" if _gchg is not None else None,
                    )
                else:
                    _gm_cols[_gi].metric(_glbl, "—")
            if _gm.get("fetched_at"):
                st.caption(f"Nguồn: Yahoo Finance · Cập nhật lúc {_gm['fetched_at'][:16].replace('T',' ')}")
        else:
            st.caption("Không tải được dữ liệu thị trường toàn cầu.")

        st.markdown("---")

        # ── 2. VNINDEX & thanh khoản thị trường ──────────────────────────────
        st.markdown("#### 📊 VNINDEX & Dòng tiền thị trường")
        _vi = fetch_vnindex_stats()
        _ff = fetch_foreign_flow()
        if _vi.get("price"):
            _vi_c1, _vi_c2, _vi_c3, _vi_c4, _vi_c5 = st.columns(5)
            _vi_price = _vi["price"]
            _vi_c1.metric("VNINDEX",      f"{_vi_price:,.2f}",
                          f"{_vi.get('chg_1d'):+.2f}%" if _vi.get('chg_1d') is not None else None)
            _vi_c2.metric("1 tuần",       f"{_vi.get('chg_5d'):+.2f}%"  if _vi.get('chg_5d')  is not None else "—")
            _vi_c3.metric("1 tháng",      f"{_vi.get('chg_20d'):+.2f}%" if _vi.get('chg_20d') is not None else "—")
            _lv = _vi.get("last_vol")
            _av = _vi.get("avg_vol_5d")
            _vi_c4.metric("KL phiên (triệu CP)*", f"{_lv/1e6:.0f}" if _lv else "—")
            _vi_c5.metric("KL TB 5P (triệu CP)*", f"{_av/1e6:.0f}" if _av else "—")
            _vi_src = _vi.get("source", "VPS")
            st.caption(f"*KL = số cổ phiếu khớp lệnh toàn thị trường (triệu CP) — nguồn VPS chart API · Cập nhật: {_vi.get('fetched_at','')[:16].replace('T',' ')}")
        else:
            st.caption("Không tải được dữ liệu VNINDEX.")

        # Khối ngoại (VN30 basket)
        if _ff.get("net_vnd") is not None:
            _ff_net = _ff["net_vnd"]
            _ff_buy = _ff.get("buy_vnd", 0)
            _ff_sell = _ff.get("sell_vnd", 0)
            _ff_color = "#00c853" if _ff_net >= 0 else "#ff1744"
            _ff_sign  = "▲ Mua ròng" if _ff_net >= 0 else "▼ Bán ròng"
            _ff_c1, _ff_c2, _ff_c3 = st.columns(3)
            _ff_c1.metric("🌏 Khối ngoại mua (VN30)", f"{_ff_buy/1e9:.0f} tỷ")
            _ff_c2.metric("Khối ngoại bán (VN30)",    f"{_ff_sell/1e9:.0f} tỷ")
            _ff_c3.metric("Khối ngoại ròng",
                          f"{abs(_ff_net)/1e9:.0f} tỷ {_ff_sign.split()[0]}",
                          f"{_ff_sign}")
            st.caption(f"Dữ liệu khối ngoại: {_ff.get('n_stocks',0)} mã VN30 · intraday · nguồn vnstock VCI")

        st.markdown("---")

        # ── 3. Vĩ mô VN — 3 nguồn: World Bank, IMF WEO, GSO/NHNN tĩnh ────────
        st.markdown("#### 🇻🇳 Vĩ mô Việt Nam")
        _wb    = fetch_wb_macro()
        _macro = _fetch_macro()
        _smacro = load_static_macro()

        _src_wb, _src_imf, _src_static = st.tabs([
            "🌐 World Bank (tự động)",
            "📌 IMF WEO (tự động + dự báo)",
            "📋 GSO / NHNN (thủ công)",
        ])

        # ── Tab World Bank ────────────────────────────────────────────────────
        with _src_wb:
            _wb_gdp   = _wb.get("gdp_growth")
            _wb_cpi   = _wb.get("cpi")
            _wb_trade = _wb.get("trade_balance")
            if _wb_gdp or _wb_cpi or _wb_trade:
                _wc1, _wc2, _wc3 = st.columns(3)
                _wc1.metric(
                    f"GDP tăng trưởng ({_wb_gdp['year'] if _wb_gdp else '—'})",
                    f"{_wb_gdp['value']:+.2f}%" if _wb_gdp else "—",
                )
                _wc2.metric(
                    f"Lạm phát CPI ({_wb_cpi['year'] if _wb_cpi else '—'})",
                    f"{_wb_cpi['value']:+.2f}%" if _wb_cpi else "—",
                )
                if _wb_trade:
                    _tb_sign = "+" if _wb_trade["value"] >= 0 else ""
                    _wc3.metric(
                        f"Cán cân thương mại ({_wb_trade['year']})",
                        f"{_tb_sign}${_wb_trade['value']/1e9:.1f}B USD",
                    )
                else:
                    _wc3.metric("Cán cân thương mại", "—")
                _wb_ts = _wb.get("fetched_at", "")[:16].replace("T", " ")
                st.caption(f"Nguồn: World Bank Open Data · lag ~1 năm · cập nhật cache: {_wb_ts}")
            else:
                st.info("Chưa tải được dữ liệu World Bank. Có thể do timeout (server WB chậm). Thử lại sau.")
                st.caption("Endpoint: api.worldbank.org/v2/country/VN/indicator/... (timeout=30s)")

        # ── Tab IMF WEO ───────────────────────────────────────────────────────
        with _src_imf:
            if _macro.get("error"):
                st.warning(f"Không tải được IMF WEO: {_macro['error']}")
            else:
                def _latest_m(series):
                    return series[0] if series else None
                def _label_yr(item):
                    if not item: return "—"
                    return f"{item['year']}" + (" 📌dự báo" if item.get("is_forecast") else "")

                _gdp_imf = _latest_m(_macro.get("gdp_growth", []))
                _cpi_imf = _latest_m(_macro.get("cpi", []))
                _ca_imf  = _latest_m(_macro.get("current_acct", []))

                _ic1, _ic2, _ic3 = st.columns(3)
                _ic1.metric(f"GDP tăng trưởng ({_label_yr(_gdp_imf)})",
                            f"{_gdp_imf['value']:+.2f}%" if _gdp_imf else "—")
                _ic2.metric(f"Lạm phát CPI ({_label_yr(_cpi_imf)})",
                            f"{_cpi_imf['value']:+.2f}%" if _cpi_imf else "—")
                _ic3.metric(f"Cán cân vãng lai ({_label_yr(_ca_imf)})",
                            f"{_ca_imf['value']:+.2f}% GDP" if _ca_imf else "—")

                # Chart GDP + CPI lịch sử nhiều năm (điểm mạnh của IMF so với WB)
                if len(_macro.get("gdp_growth", [])) >= 2:
                    import plotly.graph_objects as _go
                    _gdp_data = sorted(_macro["gdp_growth"], key=lambda x: x["year"])
                    _fig_m = _go.Figure()
                    _fig_m.add_trace(_go.Scatter(
                        x=[d["year"] for d in _gdp_data],
                        y=[d["value"] for d in _gdp_data],
                        mode="lines+markers+text",
                        text=[("📌" if d.get("is_forecast") else "") + f"{d['value']:+.1f}%" for d in _gdp_data],
                        textposition="top center",
                        name="GDP Growth %",
                        line=dict(color="#00e676", width=2),
                        marker=dict(size=7),
                    ))
                    if _macro.get("cpi"):
                        _cpi_data = sorted(_macro["cpi"], key=lambda x: x["year"])
                        _fig_m.add_trace(_go.Scatter(
                            x=[d["year"] for d in _cpi_data],
                            y=[d["value"] for d in _cpi_data],
                            mode="lines+markers",
                            name="CPI %",
                            line=dict(color="#ff6d00", width=2, dash="dot"),
                            marker=dict(size=6),
                        ))
                    _fig_m.update_layout(
                        height=200, template="plotly_dark",
                        margin=dict(l=0, r=0, t=10, b=0),
                        legend=dict(orientation="h", y=1.15),
                        yaxis=dict(ticksuffix="%"),
                    )
                    st.plotly_chart(_fig_m, use_container_width=True)
                st.caption(f"Nguồn: IMF WEO Datamapper · 📌 = dự báo/ước tính năm hiện tại · cập nhật: {_macro.get('updated','')}")

        # ── Tab GSO / NHNN (dữ liệu tĩnh, cập nhật thủ công) ─────────────────
        with _src_static:
            _sitems = _smacro.get("items", [])
            if _sitems:
                st.markdown(
                    f"<small style='color:#888'>Cập nhật thủ công: {_smacro.get('updated_at','')} "
                    f"· Nguồn: {_smacro.get('source_note','GSO/NHNN')}</small>",
                    unsafe_allow_html=True,
                )
                # Hiển thị tất cả items theo lưới 3 cột
                _n_cols = 3
                for _row_start in range(0, len(_sitems), _n_cols):
                    _row = _sitems[_row_start:_row_start + _n_cols]
                    _rcols = st.columns(_n_cols)
                    for _ci, _item in enumerate(_row):
                        _v     = _item.get("value")
                        _unit  = _item.get("unit", "")
                        _note  = _item.get("note", "")
                        _src   = _item.get("source", "")
                        _rcols[_ci].metric(
                            f"{_item['label']} ({_item.get('period','')})",
                            f"{_v:g} {_unit}" if _v is not None else "—",
                            help=f"{_note} | Nguồn: {_src}" if _note else f"Nguồn: {_src}",
                        )
            else:
                st.info("Chưa có dữ liệu. Tạo file `data/macro_static.json` để cập nhật.")
            st.caption(
                "⚠️ Dữ liệu này cập nhật thủ công theo quý từ gso.gov.vn và nhnn.gov.vn. "
                "GSO không có public JSON API — chỉ có PX-Web interface."
            )

        st.markdown(
            "<small style='color:#555'>📊 Margin, P/E thị trường, khối ngoại chi tiết: "
            "<a href='https://cafef.vn' target='_blank'>cafef.vn</a> · "
            "<a href='https://vietstock.vn' target='_blank'>vietstock.vn</a></small>",
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Cơ cấu cổ đông ───────────────────────────────────────────────────────
    st.subheader("👥 Cơ cấu cổ đông")
    with st.spinner("Đang tải cơ cấu cổ đông..."):
        sh_data = _fetch_shareholders(symbol_input)

    sh_summary = sh_data.get("summary", {})
    shareholders = sh_data.get("shareholders", [])
    officers     = sh_data.get("officers", [])
    subsidiaries = sh_data.get("subsidiaries", [])

    if sh_summary:
        mc = sh_summary.get("market_cap")
        sh_c1, sh_c2, sh_c3, sh_c4, sh_c5 = st.columns(5)
        sh_c1.metric("Vốn hóa (tỷ)", f"{mc/1e9:,.0f}" if mc else "—")
        sh_c2.metric("NĐTNN (%)", f"{sh_summary.get('foreign_pct','—')}" if sh_summary.get('foreign_pct') is not None else "—")
        sh_c3.metric("Room NN tối đa (%)", f"{sh_summary.get('foreign_max_pct','—')}" if sh_summary.get('foreign_max_pct') is not None else "—")
        sh_c4.metric("Nhà nước (%)", f"{sh_summary.get('state_pct','—')}" if sh_summary.get('state_pct') is not None else "—")
        sh_c5.metric("Free float (%)", f"{sh_summary.get('free_float_pct','—')}" if sh_summary.get('free_float_pct') is not None else "—")

    sh_tab1, sh_tab2, sh_tab3 = st.tabs(["🏦 Cổ đông lớn", "👔 Ban lãnh đạo", "🏢 Công ty con"])

    with sh_tab1:
        if not shareholders:
            st.info("Không có dữ liệu cổ đông lớn.")
        else:
            df_sh = pd.DataFrame(shareholders)
            others_pct = max(0, 100 - sum(r["percent"] or 0 for r in shareholders))
            pie_labels = [r["name"] for r in shareholders] + (["Cổ đông khác"] if others_pct > 0.1 else [])
            pie_values = [r["percent"] or 0 for r in shareholders] + ([round(others_pct, 2)] if others_pct > 0.1 else [])

            import plotly.graph_objects as _go
            fig_pie = _go.Figure(_go.Pie(
                labels=pie_labels, values=pie_values,
                textinfo="label+percent", hole=0.35,
                marker=dict(line=dict(color="#222", width=1)),
            ))
            fig_pie.update_layout(
                height=340, margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#cccccc"),
                showlegend=False,
            )
            col_pie, col_tbl = st.columns([1, 1])
            with col_pie:
                st.plotly_chart(fig_pie, use_container_width=True)
            with col_tbl:
                df_show = pd.DataFrame({
                    "Cổ đông":    [r["name"] for r in shareholders],
                    "Tỷ lệ (%)":  [r["percent"] for r in shareholders],
                    "Cập nhật":   [r["updated"] for r in shareholders],
                })
                st.dataframe(df_show, use_container_width=True, hide_index=True)

    with sh_tab2:
        if not officers:
            st.info("Không có dữ liệu ban lãnh đạo.")
        else:
            df_of = pd.DataFrame({
                "Họ tên":       [r["name"] for r in officers],
                "Chức vụ":      [r["position"] for r in officers],
                "Sở hữu (%)":   [r["percent"] for r in officers],
                "Số CP":        [f"{int(r['quantity']):,}" if r.get("quantity") else "—" for r in officers],
            })
            st.dataframe(df_of, use_container_width=True, hide_index=True)

    with sh_tab3:
        if not subsidiaries:
            st.info("Không có dữ liệu công ty con / liên kết.")
        else:
            df_sub = pd.DataFrame({
                "Tên công ty":  [r["name"] for r in subsidiaries],
                "Mã":           [r["code"] for r in subsidiaries],
                "Tỷ lệ SH (%)": [r["percent"] for r in subsidiaries],
            })
            st.dataframe(df_sub, use_container_width=True, hide_index=True)

    st.divider()

    # ── Tin tức, Sự kiện, Cổ tức ─────────────────────────────────────────────
    st.subheader("📰 Thông tin doanh nghiệp")

    with st.spinner("Đang tải tin tức & sự kiện..."):
        news_list      = _fetch_news(symbol_input)
        events_list    = _fetch_events(symbol_input)
        dividends_list = _fetch_dividends(symbol_input)
        capital_hist   = _fetch_capital_history(symbol_input)

    news_tab, events_tab, div_tab, cap_tab = st.tabs(["📰 Tin tức", "📅 Sự kiện", "💰 Cổ tức", "🏦 Lịch sử tăng vốn"])

    with news_tab:
        if not news_list:
            st.info("Không có tin tức gần đây.")
        else:
            for item in news_list:
                date_str = item.get("date", "")
                src_str  = item.get("source", "")
                title    = item.get("title", "—")
                url      = item.get("url", "")
                summary  = item.get("summary", "")
                content  = item.get("content", "")
                meta     = " · ".join(filter(None, [date_str, src_str]))

                with st.expander(f"{date_str}  {title}", expanded=False):
                    if meta:
                        st.caption(meta)
                    body = content or summary
                    if body:
                        st.markdown(body, unsafe_allow_html=True)
                    else:
                        st.caption("Không có nội dung chi tiết.")
                    if url:
                        st.markdown(f"🔗 [Xem bài gốc]({url})")
                    else:
                        q = urllib.parse.quote(title)
                        st.markdown(f"🔍 [Tìm trên Google](https://www.google.com/search?q={q})")

    with events_tab:
        if not events_list:
            st.info("Không có sự kiện sắp diễn ra.")
        else:
            for item in events_list:
                date_str = str(item.get("date", ""))[:10]
                title    = item.get("title", "—")
                ev_type  = item.get("type", "")
                value    = item.get("value", "")
                badge    = f"`{ev_type}`" if ev_type else ""
                detail   = f" — {value}" if value else ""
                st.markdown(f"**{date_str}** &nbsp; {badge} &nbsp; {title}{detail}", unsafe_allow_html=True)
                st.divider()

    with div_tab:
        if not dividends_list:
            st.info("Không có dữ liệu cổ tức.")
        else:
            try:
                df_div = pd.DataFrame(dividends_list)
                display_cols = [c for c in [
                    "exercise_date", "record_date", "ex_date",
                    "cash_dividend_rate", "dividend_amount", "ratio",
                    "issue_method", "type"
                ] if c in df_div.columns]
                if display_cols:
                    df_div = df_div[display_cols]
                st.dataframe(df_div, use_container_width=True, hide_index=True)
            except Exception:
                st.json(dividends_list)

    with cap_tab:
        if not capital_hist:
            st.info("Không có dữ liệu lịch sử tăng vốn.")
        else:
            try:
                df_cap = pd.DataFrame(capital_hist)
                rename_map = {
                    "date":            "Ngày",
                    "event_type":      "Loại sự kiện",
                    "charter_capital": "Vốn điều lệ (tỷ)",
                    "issue_share":     "Cổ phiếu phát hành",
                    "ratio":           "Tỷ lệ",
                    "notes":           "Ghi chú",
                }
                df_cap = df_cap.rename(columns={k: v for k, v in rename_map.items() if k in df_cap.columns})
                if "Vốn điều lệ (tỷ)" in df_cap.columns:
                    _cap_col = df_cap["Vốn điều lệ (tỷ)"]
                    if _cap_col.dropna().max() > 1e10:
                        df_cap["Vốn điều lệ (tỷ)"] = _cap_col / 1e9
                st.dataframe(df_cap, use_container_width=True, hide_index=True,
                    column_config={
                        "Vốn điều lệ (tỷ)": st.column_config.NumberColumn(format="%,.1f"),
                        "Tỷ lệ":             st.column_config.NumberColumn(format="%.2f"),
                    })
            except Exception:
                st.json(capital_hist)

    # ── Chatbot Cơ Bản ───────────────────────────────────────────────────────
    st.divider()
    _sh_data_for_chat = _fetch_shareholders(symbol_input)
    _render_chatbot(
        tab_key="basic",
        symbol=symbol_input,
        system_context=_build_basic_context(
            symbol_input, overview,
            fs_periods, fs_income, fs_balance, fs_cashflow,
            _sh_data_for_chat,
        ),
        placeholder="Ví dụ: làm rõ phản biện",
    )
