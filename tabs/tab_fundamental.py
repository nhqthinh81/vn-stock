"""TAB — LỌC CƠ BẢN: Lọc cổ phiếu theo checklist đầu tư toàn thị trường."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from vn_invest.fundamental_scanner import (
    load_fundamental_cache,
    filter_checklist,
)

_DATA_DIR  = Path(__file__).parent.parent / "data"
_PROG_PATH = _DATA_DIR / "fundamental_progress.json"
_SCRIPT    = Path(__file__).parent.parent / "update_fundamental.py"


def _read_progress() -> dict:
    if not _PROG_PATH.exists():
        return {}
    try:
        return json.loads(_PROG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_alive(pid: int) -> bool:
    """Kiểm tra process theo PID còn sống không (cross-platform)."""
    try:
        import os, signal
        if sys.platform == "win32":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if not handle:
                return False
            import ctypes.wintypes
            code = ctypes.wintypes.DWORD()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259  # STILL_ACTIVE
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _is_running() -> bool:
    """Kiểm tra subprocess scan có đang chạy không.

    Ưu tiên: proc trong session_state → PID trong progress file.
    PID fallback giúp UI không mất track khi Streamlit reload session.
    """
    proc: subprocess.Popen | None = st.session_state.get("fund_scan_proc")
    if proc is not None and proc.poll() is None:
        return True
    # Fallback: kiểm tra PID từ progress file (sau khi session_state bị reset)
    prog = _read_progress()
    pid = prog.get("pid")
    if pid and prog.get("status") == "running":
        return _pid_alive(int(pid))
    return False


def render(_ctx: dict) -> None:
    st.header("🏦 Lọc Cơ Bản — Toàn Thị Trường")

    fund_data, fund_updated = load_fundamental_cache()
    running = _is_running()
    # Xoá file progress cũ nếu không có subprocess đang chạy
    # để tránh hiển thị lỗi/trạng thái của lần chạy trước
    if not running and _PROG_PATH.exists():
        try:
            _PROG_PATH.unlink()
        except Exception:
            pass
    prog = _read_progress()

    # ── Metadata & nút cập nhật ──────────────────────────────────────────
    col_meta, col_btn = st.columns([3, 1])
    with col_meta:
        if fund_updated:
            st.caption(
                f"📅 Cập nhật lần cuối: **{fund_updated}** — "
                f"{len(fund_data):,} mã có dữ liệu"
            )
        elif prog.get("status") == "done":
            st.caption("✅ Quét hoàn tất. Tải lại trang để xem kết quả.")
        elif prog and prog.get("status") not in ("done", "error", "interrupted"):
            st.caption(
                f"🔄 Đang quét (checkpoint): {prog.get('done', 0):,}/{prog.get('total', 0):,} mã"
                f" — {prog.get('ts', '')}"
            )
        else:
            st.caption(
                "⚠️ Chưa có dữ liệu cơ bản. Nhấn **Cập nhật** để quét toàn thị trường "
                "(~1,500 mã, ≈ 15 phút). Hỗ trợ resume nếu bị ngắt."
            )

    with col_btn:
        if st.button(
            "🔄 Cập nhật dữ liệu cơ bản",
            disabled=running,
            key="btn_fund_scan",
            help="Chạy update_fundamental.py — resume tự động nếu đã có checkpoint.",
        ):
            # Xoá file progress cũ để tránh nhầm trạng thái
            if _PROG_PATH.exists():
                _PROG_PATH.unlink()
            import os as _os
            _env = _os.environ.copy()
            _env["PYTHONIOENCODING"] = "utf-8"
            _env["PYTHONUTF8"]       = "1"
            proc = subprocess.Popen(
                [sys.executable, str(_SCRIPT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_env,
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if sys.platform == "win32" else 0),
            )
            st.session_state["fund_scan_proc"] = proc
            st.rerun()

    # ── Progress bar (auto-refresh mỗi 3 giây khi đang quét) ────────────
    if running or prog.get("status") in ("running", "enriching"):
        pi    = prog.get("done",  0)
        pt    = prog.get("total", 1)
        ps    = prog.get("current", "...")
        pct   = prog.get("pct", 0.0) / 100
        status = prog.get("status", "running")
        if status == "enriching":
            label = f"Cập nhật thanh khoản **{ps.replace('[VOL] ', '')}** ({pi:,}/{pt:,})"
            remain = int((pt - pi) * 0.5)
        else:
            label = f"Đang quét **{ps}** ({pi:,}/{pt:,})"
            remain = int((pt - pi) * 0.6)
        st.progress(
            min(pct, 1.0),
            text=f"{label} — ước tính còn {remain // 60} phút {remain % 60} giây",
        )
        st.caption(f"Cập nhật lúc {prog.get('ts','')}")
        import time as _time
        _time.sleep(3)
        st.rerun()

    elif prog.get("status") == "error":
        st.error(f"❌ Lỗi: {prog.get('error', 'Không rõ')}")
        if prog.get("traceback"):
            with st.expander("🔍 Chi tiết lỗi (traceback)"):
                st.code(prog["traceback"], language="text")
        _log_path = _DATA_DIR / "fundamental_scan.log"
        if _log_path.exists():
            with st.expander("📋 Log quét"):
                st.code(_log_path.read_text(encoding="utf-8", errors="replace"), language="text")

    elif prog.get("status") == "done" and not fund_data:
        st.success("✅ Quét xong! Tải lại trang để xem kết quả.")

    if not fund_data:
        return

    st.divider()

    # ── Bộ lọc ───────────────────────────────────────────────────────────
    has_volume = any(r.get("avg_vol_20d") is not None for r in fund_data)
    with st.expander("⚙️ Điều chỉnh ngưỡng lọc", expanded=True):
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)
        fi_pe   = fc1.number_input("P/E tối đa",            0.0, 200.0,  25.0, 1.0,  key="fund_fi_pe")
        fi_pb   = fc2.number_input("P/B tối đa",            0.0,  20.0,   3.0, 0.5,  key="fund_fi_pb")
        fi_roe  = fc3.number_input("ROE tối thiểu (%)",     0.0,  50.0,  15.0, 1.0,  key="fund_fi_roe")
        fi_de   = fc4.number_input("Nợ/VCSH tối đa",        0.0,  20.0,   1.5, 0.5,  key="fund_fi_de")
        fi_nm   = fc5.number_input("Biên ròng tối thiểu %", -100.0, 100.0, 0.0, 1.0, key="fund_fi_nm")
        fi_nmin = fc6.number_input("Tiêu chí tối thiểu",   1, 5, 4,              key="fund_fi_nmin")

        if has_volume:
            st.markdown("**Thanh khoản** (KL TB 20 phiên — hard filter, áp dụng trước checklist)")
            fv1, fv2 = st.columns([2, 4])
            fi_vol = fv1.number_input(
                "KL tối thiểu (nghìn CP/ngày)", 0, 50000, 100, 50,
                key="fund_fi_vol",
                help="0 = bỏ qua filter thanh khoản. Mã không có dữ liệu KL bị loại khi > 0.",
            )
            fi_vol_actual = fi_vol * 1000
            if fi_vol > 0:
                n_with_vol = sum(1 for r in fund_data if r.get("avg_vol_20d") is not None)
                fv2.caption(
                    f"{n_with_vol:,}/{len(fund_data):,} mã có dữ liệu khối lượng. "
                    "Mã thiếu dữ liệu sẽ bị loại khi filter > 0."
                )
        else:
            fi_vol_actual = 0
            st.caption(
                "⚠️ Chưa có dữ liệu khối lượng. Chạy lại **Cập nhật** để bổ sung "
                "(sau khi quét cơ bản xong, hệ thống tự động cập nhật KL TB 20 phiên)."
            )

    filtered = filter_checklist(
        fund_data,
        pe_max=float(fi_pe),
        pb_max=float(fi_pb),
        roe_min=float(fi_roe),
        de_max=float(fi_de),
        net_margin_min=float(fi_nm),
        min_pass=int(fi_nmin),
        vol_min=float(fi_vol_actual),
    )

    vol_note = f", KL ≥ {fi_vol:,}K CP/ngày" if has_volume and fi_vol_actual > 0 else ""
    st.markdown(
        f"**{len(filtered):,} mã** đạt ≥{int(fi_nmin)}/5 tiêu chí cơ bản{vol_note} "
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
            "Nợ/VCSH":        f"{r['de']:.2f}x"        if r.get("de")  is not None else "—",
            "Biên ròng %":    f"{r['net_margin']:.1f}" if r.get("net_margin") is not None else "—",
            "Tăng trưởng LN": (
                f"{r['pat_growth']:+.1f}%"
                if r.get("pat_growth") is not None else "—"
            ),
            "KL TB 20P":  (
                f"{r['avg_vol_20d'] / 1e6:.1f}M"
                if r.get("avg_vol_20d") is not None else "—"
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
    valid_pe  = [r["pe"]         for r in filtered if r.get("pe")  is not None]
    valid_roe = [r["roe"]        for r in filtered if r.get("roe") is not None]
    valid_de  = [r["de"]         for r in filtered if r.get("de")  is not None]
    valid_pg  = [r["pat_growth"] for r in filtered if r.get("pat_growth") is not None]

    stat1.metric("P/E trung bình",      f"{sum(valid_pe)/len(valid_pe):.1f}"   if valid_pe  else "—")
    stat2.metric("ROE trung bình %",    f"{sum(valid_roe)/len(valid_roe):.1f}" if valid_roe else "—")
    stat3.metric("Nợ/VCSH TB",          f"{sum(valid_de)/len(valid_de):.2f}x"  if valid_de  else "—")
    pct_up = sum(1 for g in valid_pg if g > 0) / len(valid_pg) * 100 if valid_pg else 0
    stat4.metric("% mã tăng trưởng LN", f"{pct_up:.0f}%"                       if valid_pg  else "—")
