"""
Tab Vĩ Mô — Dashboard kinh tế vĩ mô Việt Nam (4 nhóm, 41 chỉ số).

Đọc từ data/macro_cache.json (cập nhật bởi update_macro.py).
Áp dụng skill vimovietnam: 4 nhóm, signal TÍCH/TRUNG/TIÊU, narrative, chart.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

from vn_invest.macro_fetcher import load_cache, save_cache

# ── constants ─────────────────────────────────────────────────────────────────

_APP_DIR = Path(__file__).parent.parent

_LABEL: dict[str, str] = {
    # Group 1
    "cpi_yoy_pct":              "CPI (YoY)",
    "gdp_growth_pct":           "Tăng trưởng GDP",
    "fdi_inflow_b_usd":         "FDI vào ròng (tỷ USD)",
    "unemployment_pct":         "Thất nghiệp",
    "pmi_manufacturing":        "PMI Sản xuất",
    # Group 2
    "lending_rate_pct":         "Lãi suất cho vay",
    "deposit_rate_pct":         "Lãi suất tiền gửi",
    "real_interest_pct":        "Lãi suất thực",
    "money_supply_growth_pct":  "Tăng trưởng M2",
    "domestic_credit_pct":      "Tín dụng nội địa / GDP",
    "usd_vnd_rate":             "Tỷ giá USD/VND",
    # Group 3
    "export_b_usd":             "Xuất khẩu (tỷ USD)",
    "import_b_usd":             "Nhập khẩu (tỷ USD)",
    "trade_bal_pct":            "XK / GDP (%)",
    # Group 4
    "fed_rate_pct":             "Lãi suất Fed (US)",
    "brent_oil_usd":            "Dầu Brent (USD/thùng)",
    "wti_oil_usd":              "Dầu WTI (USD/thùng)",
}

_UNIT: dict[str, str] = {
    "cpi_yoy_pct":             "%",
    "gdp_growth_pct":          "%",
    "fdi_inflow_b_usd":        "tỷ USD",
    "unemployment_pct":        "%",
    "pmi_manufacturing":       "điểm",
    "lending_rate_pct":        "%",
    "deposit_rate_pct":        "%",
    "real_interest_pct":       "%",
    "money_supply_growth_pct": "%",
    "domestic_credit_pct":     "% GDP",
    "usd_vnd_rate":            "VND",
    "export_b_usd":            "tỷ USD",
    "import_b_usd":            "tỷ USD",
    "trade_bal_pct":           "% GDP",
    "fed_rate_pct":            "%",
    "brent_oil_usd":           "USD/bbl",
    "wti_oil_usd":             "USD/bbl",
}

_GROUPS: list[tuple[str, str, str]] = [
    ("group1_real_economy", "🏭 Kinh tế thực",   "CPI, GDP, PMI, FDI, Việc làm"),
    ("group2_monetary",     "💰 Tiền tệ & Tín dụng", "Lãi suất, M2, Tín dụng, Tỷ giá"),
    ("group3_trade",        "🚢 Ngoại thương",    "Xuất khẩu, Nhập khẩu, Cán cân TM"),
    ("group4_global",       "🌐 Bối cảnh thế giới", "Fed, Dầu thô, USD Index"),
]

_SIGNAL_COLOR = {"TÍCH": "#4caf50", "TRUNG": "#ff9800", "TIÊU": "#f44336"}
_SIGNAL_EMOJI = {"TÍCH": "🟢", "TRUNG": "🟡", "TIÊU": "🔴"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_value(key: str, val: float) -> str:
    unit = _UNIT.get(key, "")
    if key == "usd_vnd_rate":
        return f"{int(val):,} {unit}"
    if key in ("export_b_usd", "import_b_usd", "fdi_inflow_b_usd"):
        return f"{val:,.1f} {unit}"
    return f"{val:,.2f} {unit}"


def _render_kpi_card(key: str, info: dict) -> None:
    label   = _LABEL.get(key, key)
    val     = info.get("value")
    signal  = info.get("signal", "TRUNG")
    period  = info.get("period", "")
    emoji   = _SIGNAL_EMOJI.get(signal, "🟡")

    val_str = _fmt_value(key, val) if val is not None else "—"
    delta   = f"{emoji} {signal}  ·  {period}" if period else f"{emoji} {signal}"

    st.metric(label=label, value=val_str, delta=delta, delta_color="off")


def _render_narrative(info: dict) -> None:
    narrative = info.get("narrative", "")
    if narrative:
        st.caption(f"📝 {narrative}")


def _render_history_chart(key: str, info: dict) -> None:
    history = info.get("history", [])
    if not history or len(history) < 2:
        return
    import pandas as pd
    df = pd.DataFrame(history).sort_values("period")
    df = df.dropna(subset=["value"])
    if df.empty:
        return
    label = _LABEL.get(key, key)
    st.line_chart(df.set_index("period")["value"], height=120, use_container_width=True)
    st.caption(f"Lịch sử {label} ({len(df)} điểm)")


def _summary_signals(group_data: dict) -> tuple[int, int, int]:
    tich = trung = tieu = 0
    for info in group_data.values():
        s = info.get("signal", "TRUNG")
        if s == "TÍCH":   tich += 1
        elif s == "TIÊU": tieu += 1
        else:             trung += 1
    return tich, trung, tieu


# ── render ────────────────────────────────────────────────────────────────────

def render(ctx: dict) -> None:
    st.markdown("## 🌐 Kinh Tế Vĩ Mô Việt Nam")
    st.caption(
        "Chỉ số kinh tế vĩ mô từ **World Bank, ExchangeRate-API, FRED, EIA, Trading Economics**. "
        "Dữ liệu hàng năm/tháng. Không phải khuyến nghị đầu tư."
    )

    cache = load_cache()

    # ── Header: trạng thái cache + nút cập nhật ──────────────────────────────
    if cache:
        gen_at = cache.get("generated_at", "")[:19]
        month  = cache.get("report_month", "")
        src_ok = ", ".join(cache.get("sources_ok", [])) or "—"
        st.success(f"📅 Báo cáo tháng **{month}** · Cập nhật: {gen_at} · Nguồn: {src_ok}")
    else:
        st.warning("⚠️ Chưa có dữ liệu. Nhấn **Cập Nhật** để tải lần đầu (~30 giây).")

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 Cập Nhật Dữ Liệu", type="primary"):
            with st.spinner("Đang tải dữ liệu vĩ mô..."):
                try:
                    from vn_invest.macro_fetcher import fetch_all
                    report = fetch_all(verbose=False)
                    save_cache(report)
                    st.success("✅ Đã cập nhật!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi: {e}")

    if not cache:
        return

    groups = cache.get("groups", {})

    # ── Hero KPI bar: 4 chỉ số nổi bật ───────────────────────────────────────
    _hero_keys = ["cpi_yoy_pct", "pmi_manufacturing", "usd_vnd_rate", "fed_rate_pct"]
    hero_cols = st.columns(4)
    for i, key in enumerate(_hero_keys):
        for gname, _glabel, _ in _GROUPS:
            info = groups.get(gname, {}).get(key)
            if info:
                with hero_cols[i]:
                    _render_kpi_card(key, info)
                break

    st.divider()

    # ── 4 nhóm trong subtabs ──────────────────────────────────────────────────
    tab_labels = []
    for _, glabel, gdesc in _GROUPS:
        tab_labels.append(glabel)

    tabs = st.tabs(tab_labels)

    for tab, (gkey, glabel, gdesc) in zip(tabs, _GROUPS):
        with tab:
            group_data = groups.get(gkey, {})
            if not group_data:
                st.info("Không có dữ liệu cho nhóm này. Nhấn Cập Nhật.")
                continue

            tich, trung, tieu = _summary_signals(group_data)
            st.caption(
                f"{gdesc}  ·  🟢 {tich} tích cực  🟡 {trung} trung tính  🔴 {tieu} tiêu cực"
            )

            # Hiển thị 2 cột: bên trái các card, bên phải narrative + chart
            keys = list(group_data.keys())
            n    = len(keys)
            mid  = (n + 1) // 2
            left_keys  = keys[:mid]
            right_keys = keys[mid:]

            left_col, right_col = st.columns(2)

            with left_col:
                for key in left_keys:
                    info = group_data[key]
                    _render_kpi_card(key, info)
                    _render_narrative(info)

            with right_col:
                for key in right_keys:
                    info = group_data[key]
                    _render_kpi_card(key, info)
                    _render_narrative(info)

            # Charts ở dưới (full width)
            has_chart = [k for k in keys if len(group_data[k].get("history", [])) >= 2]
            if has_chart:
                st.markdown("---")
                st.markdown("**📈 Xu hướng lịch sử**")
                chart_cols = st.columns(min(3, len(has_chart)))
                for i, key in enumerate(has_chart[:3]):
                    with chart_cols[i % len(chart_cols)]:
                        _render_history_chart(key, group_data[key])

    # ── Nguồn & Phương pháp ───────────────────────────────────────────────────
    with st.expander("ℹ️ Nguồn dữ liệu & Phương pháp"):
        st.markdown("""
**5 nguồn chính thức (theo skill vimovietnam):**

| Nguồn | Chỉ số | Tần suất |
|-------|--------|---------|
| **NSO / World Bank** | CPI, GDP, FDI, Thất nghiệp | Năm / Quý |
| **S&P Global PMI** | PMI Sản xuất (composite) | Tháng |
| **Hải quan / World Bank** | Xuất nhập khẩu, Cán cân TM | Năm |
| **VBMA / World Bank** | Lãi suất, Tín dụng, M2 | Năm |
| **FRED / EIA** | Fed rate, Dầu Brent/WTI | Tháng |

**Signal logic:**
- 🟢 **TÍCH CỰC** — Chỉ số trong vùng thuận lợi cho tăng trưởng / thị trường
- 🟡 **TRUNG TÍNH** — Chưa rõ xu hướng hoặc trong ngưỡng bình thường
- 🔴 **TIÊU CỰC** — Chỉ số vượt ngưỡng rủi ro (lạm phát cao, lãi suất cao...)

**Lưu ý:** Dữ liệu World Bank thường trễ 1-2 năm. Dùng để xem xu hướng dài hạn,
không phải số liệu tháng hiện tại. Để có số liệu tháng, chạy `update_macro.py`
sau khi có kết nối tốt đến các nguồn NSO/VBMA.
        """)
