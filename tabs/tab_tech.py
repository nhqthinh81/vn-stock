"""TAB 2 — KỸ THUẬT: Phân tích kỹ thuật cổ phiếu."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from vn_invest.indicators import get_latest_signals
from vn_invest.lstm import predict as lstm_predict, model_ready, get_model_info
from tabs.chatbot_helpers import _build_tech_context, _render_chatbot


def render(ctx: dict) -> None:
    symbol_input = ctx["symbol_input"]
    source = ctx["source"]
    _fetch_price = ctx["fetch_price"]
    _fetch_side_stats = ctx["fetch_side_stats"]

    _kt_h_col1, _kt_h_col2 = st.columns([5, 1])
    _kt_h_col1.header(f"Phân tích kỹ thuật — {symbol_input}")
    with _kt_h_col2:
        from vn_invest.watchlist import load_watchlist as _lw, add_to_watchlist as _atw
        _in_wl = symbol_input in _lw()
        if _in_wl:
            st.success("⭐ Đã theo dõi", icon=None)
        elif st.button("⭐ Theo dõi", use_container_width=True):
            _atw(symbol_input)
            st.rerun()

    with st.spinner("Đang tải dữ liệu giá..."):
        df_price = _fetch_price(symbol_input, ctx["days"], source)

    if df_price is None or df_price.empty:
        st.error(f"Không tải được dữ liệu giá cho **{symbol_input}**. Kiểm tra mã hoặc thử nguồn khác.")
    else:
        from vn_invest.screener import get_ami_scan_data as _get_ami_scan_kt, scan_symbol_realtime as _scan_rt
        _ami_kt  = _get_ami_scan_kt().get(symbol_input, {})
        _wmt_ami = _ami_kt.get("ami_wmt")
        if _wmt_ami is not None:
            df_price["weekly_macd_trend"] = int(_wmt_ami)
        sig = get_latest_signals(df_price)

        _rt_sig = None
        if _ami_kt:
            with st.spinner("Đang tải chỉ số realtime..."):
                _rt_sig = _scan_rt(symbol_input)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Giá đóng cửa", f"{sig['close']:,.0f}")
        c2.metric("RSI (14)", f"{sig['rsi']:.1f}")
        c3.metric("Tín hiệu", sig["signal"])
        c4.metric("Rủi ro", sig["risk"])
        c5.metric("Giai đoạn", sig["phase"])

        if _rt_sig and _ami_kt:
            st.divider()
            st.subheader("📡 So sánh AMI (EOD) vs vnstock (Realtime)")
            _cmp_fields = [
                ("RSI",       _ami_kt.get("ami_rsi"),      _rt_sig.get("rsi"),           ""),
                ("MACD Hist", _ami_kt.get("ami_macd_hist"),_rt_sig.get("macd_hist"),     ""),
                ("Dist EMA%", _ami_kt.get("ami_dist_ema"), _rt_sig.get("dist_ema34_pct"),""),
                ("ATR%",      _ami_kt.get("ami_atr_pct"),  _rt_sig.get("atr_pct"),       ""),
                ("Signal",    _ami_kt.get("ami_rec_label"),_rt_sig.get("signal"),        "text"),
            ]
            _cmp_cols = st.columns(len(_cmp_fields))
            for _ci, (_lbl, _av, _rv, _typ) in enumerate(_cmp_fields):
                if _av is None or _rv is None:
                    _cmp_cols[_ci].metric(_lbl, "—", "—")
                    continue
                if _typ == "text":
                    _delta = "" if str(_av) == str(_rv) else f"AMI:{_av}"
                    _cmp_cols[_ci].metric(f"{_lbl} (RT)", str(_rv), _delta or "✅ Khớp")
                else:
                    _av_f, _rv_f = float(_av), float(_rv)
                    _diff = _rv_f - _av_f
                    _pct  = abs(_diff / _av_f * 100) if _av_f != 0 else 0
                    _tag  = "✅" if _pct < 5 else ("⚠️" if _pct < 15 else "❌")
                    _cmp_cols[_ci].metric(
                        f"{_lbl} (RT)", f"{_rv_f:.3f}",
                        f"{_tag} AMI:{_av_f:.3f} ({_pct:.1f}%)",
                        delta_color="off",
                    )
            st.caption("RT = realtime từ vnstock (bar hôm nay). VolRatio không so sánh — volume intraday chưa hoàn chỉnh.")

        _ss = _fetch_side_stats(symbol_input, "VCI")
        if _ss.get("buy_vol") is not None:
            st.divider()
            st.subheader("⚖️ Áp lực mua/bán")
            _buy_pct  = _ss["buy_pct"]  or 0
            _sell_pct = _ss["sell_pct"] or 0
            _net      = _ss["net_vol"]  or 0
            _net_dir  = "🟢 Thiên mua" if _net > 0 else ("🔴 Thiên bán" if _net < 0 else "⚖️ Cân bằng")

            ss1, ss2, ss3, ss4 = st.columns(4)
            ss1.metric("Mua chủ động",  f"{_ss['buy_vol']/1e6:.2f}M CP",  f"{_buy_pct:.1f}%")
            ss2.metric("Bán chủ động",  f"{_ss['sell_vol']/1e6:.2f}M CP", f"{_sell_pct:.1f}%")
            ss3.metric("Net khối lượng",f"{_net/1e6:+.2f}M CP")
            ss4.metric("Nhận định",     _net_dir)

            st.markdown(
                f"<div style='display:flex;height:12px;border-radius:6px;overflow:hidden;margin:4px 0 8px'>"
                f"<div style='width:{_buy_pct}%;background:#00e676'></div>"
                f"<div style='width:{_sell_pct}%;background:#ff1744'></div>"
                f"</div>"
                f"<small style='color:#aaa'>🟢 Mua {_buy_pct:.1f}% &nbsp;|&nbsp; 🔴 Bán {_sell_pct:.1f}%</small>",
                unsafe_allow_html=True,
            )

        st.divider()

        lstm_result = None
        if model_ready():
            with st.spinner("Đang chạy LSTM..."):
                lstm_result = lstm_predict(symbol_input)

        if lstm_result:
            st.subheader("🤖 AI Score (LSTM)")
            ai_score = lstm_result["ai_score"]
            _sig_icons  = {"BUY-A":"🟢","BUY-B":"🟩","HOLD":"🟡","SELL-B":"🟠","SELL-A":"🔴"}
            _risk_icons = {"Low":"🟢","Medium":"🟡","High":"🔴"}

            ac1, ac2, ac3, ac4, ac5, ac6 = st.columns(6)
            ac1.metric("AI Score", f"{ai_score:.1f}/100")
            ac2.metric("AI Tín hiệu", f"{_sig_icons.get(lstm_result['signal'],'')} {lstm_result['signal']}")
            ac3.metric("AI Rủi ro", f"{_risk_icons.get(lstm_result['risk'],'')} {lstm_result['risk']}")
            ac4.metric("T+5 conf", f"{lstm_result['confidence_t5']*100:.1f}%")
            ac5.metric("T+10 conf", f"{lstm_result['confidence_t10']*100:.1f}%")
            ac6.metric("T+25 conf", f"{lstm_result['confidence_t25']*100:.1f}%")

            bar_cols = st.columns(3)
            for col, (label, key) in zip(bar_cols, [
                ("Xác suất tăng T+5",  "confidence_t5"),
                ("Xác suất tăng T+10", "confidence_t10"),
                ("Xác suất tăng T+25", "confidence_t25"),
            ]):
                val = lstm_result[key]
                color = "#00e676" if val >= 0.5 else "#ff6d00" if val >= 0.35 else "#ff1744"
                col.markdown(f"""
                <div style="margin-bottom:4px;font-size:0.85em;color:#aaa">{label}</div>
                <div style="background:#2d2d2d;border-radius:6px;height:14px;overflow:hidden">
                  <div style="width:{val*100:.1f}%;background:{color};height:100%;border-radius:6px"></div>
                </div>
                <div style="font-size:0.8em;color:#ddd;text-align:right">{val*100:.1f}%</div>
                """, unsafe_allow_html=True)
            st.caption(f"Model {lstm_result['model_version']} | {lstm_result['rows_used']} phiên Amibroker")
        elif model_ready():
            st.warning(f"Không có data Amibroker cho **{symbol_input}**")
        else:
            info = get_model_info()
            st.info("LSTM chưa sẵn sàng. Vào tab **🤖 Model AI** để train." if info["version"] == "none"
                    else f"Model {info['version']} tìm thấy — kiểm tra TensorFlow.")

        st.divider()

        col_left, col_right = st.columns(2)
        with col_left:
            st.subheader("Thông số kỹ thuật (Rule-based)")
            _tech_rows = [
                {"Chỉ số": "Điểm kỹ thuật",    "Giá trị": f"{sig['tech_score']:.1f}/100",  "Lý thuyết": "Momentum tổng hợp"},
                {"Chỉ số": "MACD Histogram",    "Giá trị": f"{sig['macd_hist']:.4f}",        "Lý thuyết": "Appel (1979)"},
                {"Chỉ số": "Dist EMA34 (%)",    "Giá trị": f"{sig['dist_ema34_pct']:.2f}%",  "Lý thuyết": "Elder Triple Screen"},
                {"Chỉ số": "Log Return",        "Giá trị": f"{sig['log_return']:.4f}",       "Lý thuyết": "Random Walk / CAPM"},
            ]
            _atr = sig.get("atr_pct")
            _bbw = sig.get("bb_width_pct")
            _vol = sig.get("volume_ratio")
            if _atr is not None:
                _tech_rows.append({"Chỉ số": "ATR% (Volatility)", "Giá trị": f"{_atr:.2f}%",
                                    "Lý thuyết": "Wilder ATR (1978)"})
            if _bbw is not None:
                _tech_rows.append({"Chỉ số": "BB Width% (Độ nén)", "Giá trị": f"{_bbw:.1f}%",
                                    "Lý thuyết": "Bollinger Band (1983)"})
            if _vol is not None:
                _tech_rows.append({"Chỉ số": "Volume Ratio",      "Giá trị": f"{_vol:.2f}x",
                                    "Lý thuyết": "Granville's Law (1963)"})
            st.dataframe(pd.DataFrame(_tech_rows), use_container_width=True, hide_index=True)

        with col_right:
            st.subheader("Phiên giao dịch gần nhất")
            _cols = [c for c in ["time","open","high","low","close","volume","rsi","volume_ratio"] if c in df_price.columns]
            last10 = df_price.tail(11)[_cols].copy()
            last10 = last10.sort_values("time", ascending=False).reset_index(drop=True)
            last10["change_pct"] = (last10["close"] - last10["close"].shift(-1)) / last10["close"].shift(-1) * 100
            last10 = last10.head(10)

            def _row_bg(vr):
                if pd.isna(vr):  return ""
                if vr >= 2.5:    return "background:rgba(0,230,118,0.18)"
                if vr >= 1.75:   return "background:rgba(0,230,118,0.10)"
                if vr >= 1.25:   return "background:rgba(255,215,64,0.08)"
                if vr < 0.5:     return "background:rgba(255,82,82,0.12)"
                return ""

            def _vol_html(vol, vr):
                if pd.isna(vol) or vol <= 0: return "—"
                vol_s = f"{vol/1e6:.2f}M"
                if pd.isna(vr):
                    return vol_s
                if vr >= 2.5:   label, c = f"×{vr:.1f}", "#00e676"
                elif vr >= 1.75: label, c = f"×{vr:.1f}", "#69f0ae"
                elif vr >= 1.25: label, c = f"×{vr:.1f}", "#ffd740"
                elif vr < 0.5:  label, c = f"×{vr:.1f}", "#ff5252"
                else:           label, c = f"×{vr:.1f}", "#777"
                return f'{vol_s} <span style="font-size:0.78em;color:{c}">{label}</span>'

            def _pct_html(v):
                if pd.isna(v): return "—"
                color = "#00e676" if v > 0 else ("#ff5252" if v < 0 else "#aaa")
                arrow = "▲" if v > 0 else ("▼" if v < 0 else "—")
                return f'<span style="color:{color};font-weight:600">{arrow} {abs(v):.2f}%</span>'

            rows_html = []
            for _, r in last10.iterrows():
                vr     = r.get("volume_ratio") if "volume_ratio" in r.index else float("nan")
                bg     = _row_bg(vr)
                date_s  = str(r["time"])[:10] if pd.notna(r.get("time")) else "—"
                close_s = f"{r['close']:,.0f}" if pd.notna(r["close"]) else "—"
                open_s  = f"{r['open']:,.0f}"  if pd.notna(r.get("open"))  else "—"
                high_s  = f"{r['high']:,.0f}"  if pd.notna(r.get("high"))  else "—"
                low_s   = f"{r['low']:,.0f}"   if pd.notna(r.get("low"))   else "—"
                vol_s   = _vol_html(r.get("volume"), vr)
                rsi_s   = f"{r['rsi']:.1f}" if pd.notna(r.get("rsi")) else "—"
                pct_s   = _pct_html(r["change_pct"])
                rows_html.append(
                    f"<tr style='{bg}'>"
                    f"<td>{date_s}</td>"
                    f"<td style='text-align:right'>{close_s}</td>"
                    f"<td style='text-align:center'>{pct_s}</td>"
                    f"<td style='text-align:right;color:#aaa'>{open_s}</td>"
                    f"<td style='text-align:right;color:#26a69a'>{high_s}</td>"
                    f"<td style='text-align:right;color:#ef5350'>{low_s}</td>"
                    f"<td style='text-align:right'>{vol_s}</td>"
                    f"<td style='text-align:center;color:#ce93d8'>{rsi_s}</td></tr>"
                )

            legend = (
                '<div style="font-size:0.73em;color:#666;margin-bottom:4px">'
                '<span style="background:rgba(0,230,118,0.18);padding:1px 6px;border-radius:3px;margin-right:6px">≥2.5×</span>'
                '<span style="background:rgba(0,230,118,0.10);padding:1px 6px;border-radius:3px;margin-right:6px">≥1.75×</span>'
                '<span style="background:rgba(255,215,64,0.08);padding:1px 6px;border-radius:3px;margin-right:6px">≥1.25×</span>'
                '<span style="background:rgba(255,82,82,0.12);padding:1px 6px;border-radius:3px;margin-right:6px">&lt;0.5×</span>'
                '— Vol / SMA20</div>'
            )
            table_html = """
            <style>
            .session-table{width:100%;border-collapse:collapse;font-size:0.82em}
            .session-table th{background:#1e2130;color:#888;font-weight:500;
                padding:5px 8px;border-bottom:1px solid #333;text-align:center}
            .session-table td{padding:5px 8px;border-bottom:1px solid #1a1a2e}
            </style>
            """ + legend + """
            <table class="session-table">
            <thead><tr>
              <th>Ngày</th><th>Đóng cửa</th><th>%</th>
              <th>Mở</th><th>Cao</th><th>Thấp</th><th>KL (SMA20)</th><th>RSI</th>
            </tr></thead>
            <tbody>""" + "".join(rows_html) + "</tbody></table>"
            st.markdown(table_html, unsafe_allow_html=True)

        # ── Mẫu hình giá (Bulkowski VN) ──────────────────────────────────────
        from vn_invest.indicators import detect_chart_patterns, detect_candle_patterns
        _cp_list  = detect_chart_patterns(df_price)
        _can_list, _can_tf = detect_candle_patterns(df_price)

        if _cp_list or _can_list:
            st.divider()
            st.subheader("📐 Mẫu hình giá (Bulkowski VN)")

            _DIR_COLOR = {"bull": "#00e676", "bear": "#ff5252", "neutral": "#ffd740"}
            _DIR_LABEL = {"bull": "Tăng ▲", "bear": "Giảm ▼", "neutral": "Trung tính →"}

            if _cp_list:
                st.markdown("**Chart Patterns (Price Action)**")
                cp_cols = st.columns(min(len(_cp_list), 3))
                for idx, (name, direction, rate) in enumerate(_cp_list):
                    short = name.split("(")[0].strip()
                    color = _DIR_COLOR.get(direction, "#aaa")
                    label = _DIR_LABEL.get(direction, direction)
                    cp_cols[idx % 3].markdown(
                        f"""<div style="background:#1e2130;border-left:4px solid {color};
                        border-radius:6px;padding:10px 14px;margin-bottom:8px">
                        <div style="font-size:0.95em;font-weight:600;color:{color}">{short}</div>
                        <div style="font-size:0.82em;color:#aaa;margin-top:2px">{label}</div>
                        <div style="margin-top:6px">
                          <span style="font-size:1.1em;font-weight:700;color:#fff">{rate}%</span>
                          <span style="font-size:0.78em;color:#888;margin-left:6px">xác suất thành công (VN)</span>
                        </div></div>""",
                        unsafe_allow_html=True,
                    )

            if _can_list:
                st.markdown(f"**Candle Patterns** *(khung {_can_tf})*")
                can_cols = st.columns(min(len(_can_list), 3))
                for idx, (name, direction, rate) in enumerate(_can_list):
                    short = name.split("(")[0].strip()
                    color = _DIR_COLOR.get(direction, "#aaa")
                    label = _DIR_LABEL.get(direction, direction)
                    can_cols[idx % 3].markdown(
                        f"""<div style="background:#1e2130;border-left:4px solid {color};
                        border-radius:6px;padding:10px 14px;margin-bottom:8px">
                        <div style="font-size:0.95em;font-weight:600;color:{color}">{short}</div>
                        <div style="font-size:0.82em;color:#aaa;margin-top:2px">{label}</div>
                        <div style="margin-top:6px">
                          <span style="font-size:1.1em;font-weight:700;color:#fff">{rate}%</span>
                          <span style="font-size:0.78em;color:#888;margin-left:6px">xác suất thành công (VN)</span>
                        </div></div>""",
                        unsafe_allow_html=True,
                    )
        else:
            st.divider()
            st.subheader("📐 Mẫu hình giá (Bulkowski VN)")
            st.caption("Không phát hiện mẫu hình rõ ràng trong 60 phiên gần nhất.")

        # ── Khối ngoại 30 ngày ───────────────────────────────────────────────────
        st.divider()
        st.subheader("🌏 Khối ngoại (30 ngày)")
        with st.spinner("Đang tải dữ liệu khối ngoại..."):
            from vn_invest.data import get_foreign_net_buy
            _fn = get_foreign_net_buy(symbol_input, days=30)
        if _fn:
            _fn_net   = _fn["net_buy_vol"]
            _fn_color = "#00e676" if _fn_net >= 0 else "#ff5252"
            _fn_icon  = "▲" if _fn_net >= 0 else "▼"
            _fn_label = "Mua ròng" if _fn_net >= 0 else "Bán ròng"
            _fn_cols  = st.columns(4)
            _fn_cols[0].metric("Mua ròng phiên (CP)",   f"{_fn['net_buy_vol']:+,.0f}")
            _fn_cols[1].metric("Mua ròng phiên (tỷ ₫)", f"{_fn['net_buy_val']/1e9:+.2f}")
            _fn_cols[2].metric("KL Mua NN",              f"{_fn['buy_vol']:,.0f}")
            _fn_cols[3].metric("KL Bán NN",              f"{_fn['sell_vol']:,.0f}")
            if _fn.get("foreign_room") is not None:
                _room = _fn["foreign_room"]
                st.caption(f"🏦 Room nước ngoài còn lại: **{_room:,.0f} CP**")
            st.markdown(
                f'<p style="color:{_fn_color};font-weight:600">'
                f'{_fn_icon} Phiên này khối ngoại đang <b>{_fn_label}</b></p>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("Không có dữ liệu khối ngoại cho mã này.")

        st.divider()

        st.subheader("Biểu đồ giá")
        df_plot = df_price.dropna(subset=["close"]).copy()

        fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                            row_heights=[0.55, 0.25, 0.20], vertical_spacing=0.03)

        fig.add_trace(go.Candlestick(
            x=df_plot["time"], open=df_plot["open"], high=df_plot["high"],
            low=df_plot["low"], close=df_plot["close"], name="Giá",
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
        ), row=1, col=1)

        for col_name, color, dash in [("sma20","#ffd740","solid"),("sma50","#40c4ff","solid"),("ema34","#ff80ab","dot")]:
            d = df_plot.dropna(subset=[col_name])
            fig.add_trace(go.Scatter(x=d["time"], y=d[col_name], name=col_name.upper(),
                                     line=dict(color=color, width=1.2, dash=dash)), row=1, col=1)

        rsi_d = df_plot.dropna(subset=["rsi"])
        fig.add_trace(go.Scatter(x=rsi_d["time"], y=rsi_d["rsi"], name="RSI",
                                 line=dict(color="#ce93d8", width=1.5),
                                 fill="tozeroy", fillcolor="rgba(206,147,216,0.1)"), row=2, col=1)
        fig.add_hline(y=70, line_color="#ef5350", line_dash="dash", line_width=1, row=2, col=1)
        fig.add_hline(y=30, line_color="#26a69a", line_dash="dash", line_width=1, row=2, col=1)

        macd_d = df_plot.dropna(subset=["macd_hist"])
        fig.add_trace(go.Bar(x=macd_d["time"], y=macd_d["macd_hist"], name="MACD Hist",
                             marker_color=["#26a69a" if v >= 0 else "#ef5350" for v in macd_d["macd_hist"]]),
                      row=3, col=1)

        fig.update_layout(height=700, template="plotly_dark", showlegend=True,
                          xaxis_rangeslider_visible=False,
                          margin=dict(l=0, r=0, t=10, b=0),
                          legend=dict(orientation="h", y=1.02, x=0))
        fig.update_yaxes(title_text="Giá (nghìn VNĐ)", row=1, col=1)
        fig.update_yaxes(title_text="RSI", row=2, col=1, range=[0, 100])
        fig.update_yaxes(title_text="MACD", row=3, col=1)
        st.plotly_chart(fig, use_container_width=True)

        # ── Chatbot Kỹ Thuật ─────────────────────────────────────────────
        st.divider()
        _df10 = df_price.tail(10)
        _tech_hist = {
            "periods":   [str(t)[:10] for t in _df10["time"]] if "time" in _df10.columns else [str(i) for i in _df10.index],
            "close":     list(_df10["close"])     if "close"    in _df10.columns else [],
            "volume":    list(_df10["volume"])    if "volume"   in _df10.columns else [],
            "rsi":       list(_df10["rsi"])       if "rsi"      in _df10.columns else [],
            "ema34":     list(_df10["ema34"])     if "ema34"    in _df10.columns else [],
            "macd_hist": list(_df10["macd_hist"]) if "macd_hist" in _df10.columns else [],
        }
        _ctx_tech = _build_tech_context(symbol_input, sig, _tech_hist)
        with st.expander("🔍 Debug: Context gửi cho Claude", expanded=False):
            st.code(_ctx_tech, language="text")
        _render_chatbot(
            tab_key="tech",
            symbol=symbol_input,
            system_context=_ctx_tech,
            placeholder="Ví dụ: phân tích xu hướng kỹ thuật gần đây",
        )
