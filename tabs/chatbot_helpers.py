"""Chatbot helpers dùng chung cho tab_basic và tab_tech."""
import json
from pathlib import Path

import streamlit as st


_CHAT_HISTORY_FILE = Path(__file__).parent.parent / "data" / "chat_history.json"


def _build_basic_context(symbol: str, overview: dict, fs_periods: list,
                          fs_income: dict, fs_balance: dict, fs_cashflow: dict,
                          shareholders: dict) -> str:
    """Xây dựng ngữ cảnh cho chatbot Tab Cơ Bản."""
    lines = [
        f"Cổ phiếu: {symbol}",
        f"Tên công ty: {overview.get('company_name') or overview.get('organ_name', '')}",
        f"Ngành: {overview.get('industry_name') or overview.get('sector', '')}",
        f"Sàn: {overview.get('exchange', '')}",
        f"CEO: {overview.get('ceo_name', '')}",
        f"Nhân viên: {overview.get('number_of_employees', '')}",
        "",
    ]
    # Tóm tắt 2 kỳ gần nhất BCTC
    if fs_periods:
        lines.append(f"Các kỳ BCTC: {', '.join(fs_periods[:4])}")
        def _v(store, iid):
            item = store.get(iid, {})
            v = item.get("values", [None])[0]
            return f"{v/1e9:,.1f} tỷ" if v else "—"
        lines += [
            f"Doanh thu (kỳ gần nhất): {_v(fs_income, 'isa1')}",
            f"Lợi nhuận sau thuế: {_v(fs_income, 'isa20')}",
            f"Tổng tài sản: {_v(fs_balance, 'bsa53')}",
            f"Nợ phải trả: {_v(fs_balance, 'bsa54')}",
            f"Vay ngắn hạn: {_v(fs_balance, 'bsa56')}",
            f"Vay dài hạn: {_v(fs_balance, 'bsa71')}",
            f"Vốn chủ sở hữu: {_v(fs_balance, 'bsa78')}",
            f"CFO (HDKD): {_v(fs_cashflow, 'cfa18')}",
            f"Tiền thu từ vay mới: {_v(fs_cashflow, 'cfa29')}",
            f"Tiền trả nợ gốc: {_v(fs_cashflow, 'cfa30')}",
        ]
    # Cổ đông lớn
    if shareholders:
        sh_list = shareholders.get("shareholders") or []
        if sh_list:
            lines.append("\nCổ đông lớn:")
            for sh in sh_list[:5]:
                pct = sh.get("percentage") or sh.get("share_own_percent", "")
                lines.append(f"  - {sh.get('name','')}: {pct}%")
        off_list = shareholders.get("officers") or []
        if off_list:
            lines.append("Ban lãnh đạo:")
            for of in off_list[:4]:
                own = of.get("share_own_percent", "")
                own_str = f" ({own}%)" if own else ""
                lines.append(f"  - {of.get('name','')} — {of.get('position') or of.get('title','')}{own_str}")
    return "\n".join(lines)


def _build_tech_context(symbol: str, sig: dict, hist: dict) -> str:
    """Xây dựng ngữ cảnh cho chatbot Tab Kỹ Thuật."""
    import math

    def _fs(v, fmt="{:,.0f}"):
        try:
            return "—" if v is None or math.isnan(float(v)) else fmt.format(float(v))
        except Exception:
            return "—"

    lines = [
        f"Cổ phiếu: {symbol}",
        f"Giá hiện tại: {sig.get('close', '—')}",
        f"RSI(14): {sig.get('rsi', '—')}",
        f"MACD Histogram: {sig.get('macd_hist', '—')}",
        f"Dist EMA34%: {sig.get('dist_ema34_pct', '—')}",
        f"Log Return: {sig.get('log_return', '—')}",
        f"Signal Class: {sig.get('signal', '—')}",
        f"Risk Level: {sig.get('risk', '—')}",
        f"Phase: {sig.get('phase', '—')}",
        f"Tech Score: {sig.get('tech_score', '—')}",
        "",
        "10 phiên gần nhất:",
    ]
    periods   = hist.get("periods", [])
    closes    = hist.get("close", [])
    volumes   = hist.get("volume", [])
    rsis      = hist.get("rsi", [])
    ema34s    = hist.get("ema34", [])
    macds     = hist.get("macd_hist", [])
    for i in range(min(10, len(periods))):
        d = periods[i]   if i < len(periods)  else ""
        c = closes[i]    if i < len(closes)   else None
        v = volumes[i]   if i < len(volumes)  else None
        r = rsis[i]      if i < len(rsis)     else None
        e = ema34s[i]    if i < len(ema34s)   else None
        m = macds[i]     if i < len(macds)    else None
        v_raw = _fs(v, "{:,.0f}") if v is None else _fs(float(v) / 1e6, "{:.1f}") + "M"
        lines.append(f"  {d}: Đóng {_fs(c)} | EMA34 {_fs(e)} | KL {v_raw} | RSI {_fs(r, '{:.1f}')} | MACD {_fs(m, '{:+.3f}')}")
    return "\n".join(lines)


def _load_chat_history_file() -> list:
    try:
        if _CHAT_HISTORY_FILE.exists():
            return json.loads(_CHAT_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_chat_turn(tab_key: str, symbol: str, q: str, a: str):
    from datetime import datetime
    records = _load_chat_history_file()
    records.append({
        "date":   datetime.now().strftime("%Y-%m-%d"),
        "time":   datetime.now().strftime("%H:%M"),
        "tab":    tab_key,
        "symbol": symbol,
        "q":      q,
        "a":      a,
    })
    # Giữ tối đa 500 turn gần nhất
    records = records[-500:]
    try:
        _CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CHAT_HISTORY_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _render_chatbot(tab_key: str, symbol: str, system_context: str, placeholder: str = "Nhập câu hỏi..."):
    """Render chatbot có ngữ cảnh sẵn cho một tab. Dùng streaming để không treo UI."""
    from vn_invest.analyzer import _call_claude_stream

    st.markdown("#### 💬 Chat với AI — Phân tích chuyên sâu")
    st.caption(f"Model: Claude Sonnet (streaming) | Ngữ cảnh: dữ liệu {symbol} đang hiển thị")

    chat_key = f"chat_{tab_key}_{symbol}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    # Hiển thị lịch sử session hiện tại
    for turn in st.session_state[chat_key]:
        with st.chat_message("user"):
            st.markdown(turn["q"])
        with st.chat_message("assistant"):
            st.markdown(turn["a"])

    user_q = st.chat_input(placeholder, key=f"input_{tab_key}_{symbol}")
    if user_q:
        sys_prompt = (
            "Bạn là chuyên gia phân tích chứng khoán Việt Nam với khả năng suy luận sâu.\n"
            "DỮ LIỆU CỔ PHIẾU ĐÃ ĐƯỢC CUNG CẤP ĐẦY ĐỦ TRONG CONTEXT NÀY — hãy phân tích dựa trên đó.\n"
            "TUYỆT ĐỐI KHÔNG được nói 'không có dữ liệu', 'cần thêm OHLCV', hay yêu cầu user cung cấp thêm data.\n"
            "Nếu một chỉ số cụ thể không có (ví dụ High/Low) thì bỏ qua chỉ số đó, không liệt kê danh sách 'cần có'.\n"
            "Tính toán từ số liệu có sẵn (Close, Volume, RSI, MACD, EMA34).\n\n"
            f"## DỮ LIỆU CỔ PHIẾU HIỆN TẠI\n{system_context}"
        )

        msgs = []
        for turn in st.session_state[chat_key][-6:]:
            msgs.append({"role": "user",      "content": turn["q"]})
            msgs.append({"role": "assistant", "content": turn["a"]})
        msgs.append({"role": "user", "content": user_q})

        with st.chat_message("user"):
            st.markdown(user_q)

        with st.chat_message("assistant"):
            try:
                ans = st.write_stream(
                    _call_claude_stream(
                        prompt="",
                        model="claude-sonnet-4-6",
                        max_tokens=4096,
                        system=sys_prompt,
                        messages=msgs,
                    )
                )
            except Exception as e:
                ans = f"⚠️ Lỗi: {e}"
                st.error(ans)

        st.session_state[chat_key].append({"q": user_q, "a": ans})
        _save_chat_turn(tab_key, symbol, user_q, ans)
        st.rerun()

    if st.session_state[chat_key]:
        if st.button("🗑 Xóa lịch sử chat", key=f"clear_{tab_key}_{symbol}"):
            st.session_state[chat_key] = []
            st.rerun()

    # ── Xem lịch sử đã lưu ───────────────────────────────────────────────────
    with st.expander("📋 Lịch sử chat đã lưu", expanded=False):
        all_records = _load_chat_history_file()
        if not all_records:
            st.caption("Chưa có lịch sử nào được lưu.")
        else:
            # Bộ lọc
            fc1, fc2, fc3 = st.columns(3)
            all_dates   = sorted({r["date"] for r in all_records}, reverse=True)
            all_symbols = sorted({r["symbol"] for r in all_records})
            filter_date   = fc1.selectbox("Ngày", ["Tất cả"] + all_dates,  key=f"fdate_{tab_key}")
            filter_symbol = fc2.selectbox("Mã",   ["Tất cả"] + all_symbols, key=f"fsym_{tab_key}")
            filter_tab    = fc3.selectbox("Tab",   ["Tất cả", "basic", "tech"], key=f"ftab_{tab_key}")

            filtered = [
                r for r in reversed(all_records)
                if (filter_date   == "Tất cả" or r["date"]   == filter_date)
                and (filter_symbol == "Tất cả" or r["symbol"] == filter_symbol)
                and (filter_tab    == "Tất cả" or r["tab"]    == filter_tab)
            ]

            if not filtered:
                st.caption("Không có kết quả khớp bộ lọc.")
            else:
                st.caption(f"{len(filtered)} lượt chat")
                for i, r in enumerate(filtered[:50]):
                    tab_lbl = "Cơ Bản" if r["tab"] == "basic" else "Kỹ Thuật"
                    ans_full = r["a"]
                    # Lấy dòng kết luận: ưu tiên đoạn bắt đầu bằng **Kết luận / **Tóm lại / **Nhận định
                    import re as _re
                    _concl_match = _re.search(
                        r"(\*\*(?:Kết luận|Tóm lại|Nhận định|Tổng kết|Khuyến nghị)[^*]*\*\*[^\n]*(?:\n(?!\n).{0,300})*)",
                        ans_full, _re.IGNORECASE
                    )
                    conclusion = _concl_match.group(0).strip() if _concl_match else ""

                    with st.expander(
                        f"[{r['date']} {r['time']}] {r['symbol']} · {tab_lbl} — {r['q'][:80]}",
                        expanded=False,
                    ):
                        st.markdown(f"**Q:** {r['q']}")
                        st.markdown("---")
                        st.markdown(ans_full)   # toàn bộ câu trả lời, có markdown
                        if conclusion:
                            st.info(f"📌 **Kết luận:** {conclusion}")
            if st.button("🗑 Xóa toàn bộ lịch sử đã lưu", key=f"del_hist_{tab_key}"):
                _CHAT_HISTORY_FILE.unlink(missing_ok=True)
                st.rerun()
