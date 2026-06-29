"""TAB — LỌC CƠ BẢN: Lọc cổ phiếu theo checklist đầu tư toàn thị trường."""
import threading

import pandas as pd
import streamlit as st

from vn_invest.fundamental_scanner import (
    load_fundamental_cache,
    load_checkpoint_meta,
    scan_all_fundamentals,
    filter_checklist,
)


def render(ctx: dict) -> None:  # noqa: ARG001
    st.header("🏦 Lọc Cơ Bản — Toàn Thị Trường")

    # ── Metadata & nút cập nhật ──────────────────────────────────────────
    fund_data, fund_updated = load_fundamental_cache()
    is_running = st.session_state.get("fund_scan_running", False)

    col_meta, col_btn = st.columns([3, 1])
    with col_meta:
        if fund_updated:
            st.caption(
                f"📅 Cập nhật lần cuối: **{fund_updated}** — "
                f"{len(fund_data):,} mã có dữ liệu"
            )
        else:
            ckpt = load_checkpoint_meta()
            if ckpt:
                st.caption(
                    f"🔄 Scan đang dở (checkpoint): {ckpt['done']:,}/{ckpt['total']:,} mã "
                    f"— lưu lúc {ckpt['saved_at']}"
                )
            else:
                st.caption(
                    "⚠️ Chưa có dữ liệu cơ bản. Nhấn **Cập nhật** để quét toàn thị trường "
                    "(~1,500 mã, ≈ 15 phút). Hỗ trợ resume nếu bị ngắt giữa chừng."
                )

    with col_btn:
        if st.button(
            "🔄 Cập nhật dữ liệu cơ bản",
            disabled=is_running,
            key="btn_fund_scan",
            help="Quét ~1,500 mã từ vnstock KBS. Resume tự động nếu bị ngắt.",
        ):
            st.session_state["fund_scan_running"]  = True
            st.session_state["fund_scan_progress"] = (0, 1, "Đang khởi động...")

            def _run():
                def _cb(i, total, sym):
                    st.session_state["fund_scan_progress"] = (i, total, sym)
                scan_all_fundamentals(progress_callback=_cb, resume=True)
                st.session_state["fund_scan_running"]  = False
                st.session_state["fund_scan_progress"] = None

            threading.Thread(target=_run, daemon=True).start()
            st.rerun()

    # ── Progress bar khi đang quét ───────────────────────────────────────
    if is_running:
        prog = st.session_state.get("fund_scan_progress") or (0, 1, "...")
        pi, pt, ps = prog
        pct = pi / max(pt, 1)
        remain_sec = int((pt - pi) * 0.6)
        st.progress(
            pct,
            text=f"Đang quét **{ps}** ({pi:,}/{pt:,}) — "
                 f"ước tính còn {remain_sec // 60} phút {remain_sec % 60} giây",
        )
        st.button("↻ Làm mới tiến độ", key="btn_fund_refresh")
        ckpt2 = load_checkpoint_meta()
        if ckpt2.get("done", 0) > 0:
            st.caption(f"Checkpoint: {ckpt2['done']:,}/{ckpt2['total']:,} mã đã xử lý")

    if not fund_data:
        return

    st.divider()

    # ── Bộ lọc ───────────────────────────────────────────────────────────
    with st.expander("⚙️ Điều chỉnh ngưỡng lọc", expanded=True):
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)
        fi_pe   = fc1.number_input("P/E tối đa",            0.0, 200.0,  25.0, 1.0,  key="fi_pe")
        fi_pb   = fc2.number_input("P/B tối đa",            0.0,  20.0,   3.0, 0.5,  key="fi_pb")
        fi_roe  = fc3.number_input("ROE tối thiểu (%)",     0.0,  50.0,  15.0, 1.0,  key="fi_roe")
        fi_de   = fc4.number_input("Nợ/VCSH tối đa",        0.0,  20.0,   1.5, 0.5,  key="fi_de")
        fi_nm   = fc5.number_input("Biên ròng tối thiểu %", -100.0, 100.0, 0.0, 1.0, key="fi_nm")
        fi_nmin = fc6.number_input("Tiêu chí tối thiểu",   1, 5, 4,             key="fi_nmin")

    filtered = filter_checklist(
        fund_data,
        pe_max=float(fi_pe),
        pb_max=float(fi_pb),
        roe_min=float(fi_roe),
        de_max=float(fi_de),
        net_margin_min=float(fi_nm),
        min_pass=int(fi_nmin),
    )

    st.markdown(
        f"**{len(filtered):,} mã** đạt ≥{int(fi_nmin)}/5 tiêu chí cơ bản "
        f"(từ tổng {len(fund_data):,} mã có dữ liệu)"
    )

    if not filtered:
        st.info("Không có mã nào đạt tiêu chí. Thử nới lỏng ngưỡng lọc.")
        return

    # ── Bảng kết quả ─────────────────────────────────────────────────────
    rows = []
    for r in filtered:
        rows.append({
            "Mã":             r["symbol"],
            "Kỳ báo cáo":    r.get("period", "—"),
            "P/E":            f"{r['pe']:.1f}"         if r.get("pe")  is not None else "—",
            "P/B":            f"{r['pb']:.1f}"         if r.get("pb")  is not None else "—",
            "ROE %":          f"{r['roe']:.1f}"        if r.get("roe") is not None else "—",
            "Nợ/VCSH":        f"{r['de']:.2f}x"       if r.get("de")  is not None else "—",
            "Biên ròng %":    f"{r['net_margin']:.1f}" if r.get("net_margin") is not None else "—",
            "Tăng trưởng LN": (
                f"{r['pat_growth']:+.1f}%"
                if r.get("pat_growth") is not None else "—"
            ),
            "Tiêu chí":      f"{r.get('n_pass', 0)}/5",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Mã":            st.column_config.TextColumn(width="small"),
            "Kỳ báo cáo":   st.column_config.TextColumn(width="small"),
            "Tiêu chí":     st.column_config.TextColumn(width="small"),
            "Tăng trưởng LN": st.column_config.TextColumn(width="medium"),
        },
    )

    # ── Thống kê nhanh ───────────────────────────────────────────────────
    st.divider()
    st.markdown("#### Thống kê bộ lọc")
    stat1, stat2, stat3, stat4 = st.columns(4)
    valid_pe  = [r["pe"]          for r in filtered if r.get("pe")  is not None]
    valid_roe = [r["roe"]         for r in filtered if r.get("roe") is not None]
    valid_de  = [r["de"]          for r in filtered if r.get("de")  is not None]
    valid_pg  = [r["pat_growth"]  for r in filtered if r.get("pat_growth") is not None]

    stat1.metric("P/E trung bình",   f"{sum(valid_pe)/len(valid_pe):.1f}"   if valid_pe  else "—")
    stat2.metric("ROE trung bình %", f"{sum(valid_roe)/len(valid_roe):.1f}" if valid_roe else "—")
    stat3.metric("Nợ/VCSH TB",       f"{sum(valid_de)/len(valid_de):.2f}x"  if valid_de  else "—")
    pct_growth = sum(1 for g in valid_pg if g > 0) / len(valid_pg) * 100 if valid_pg else 0
    stat4.metric("% mã tăng trưởng LN", f"{pct_growth:.0f}%"               if valid_pg  else "—")
