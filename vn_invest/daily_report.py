"""Báo cáo tổng hợp lãi/lỗ cuối ngày cho bot phái sinh VN30F1M.

Nguồn dữ liệu: journal CSV schema v4 (có cột trade_id) do phaisinh_tab ghi ra.
Mỗi lệnh gồm 2 bản ghi cùng trade_id: "MỞ <chiều>" và "ĐÓNG <chiều>".

Không import ngược phaisinh_tab ở phía UI: phaisinh_tab import module này bên
trong hàm render để tránh vòng lặp import.
"""
from __future__ import annotations

import os
import smtplib
from datetime import date, datetime
from email.message import EmailMessage

import pandas as pd

from .alerter import fmt_vn                      # dùng chung, chuẩn VN
from .phaisinh_tab import _FEE_PTS, _PT_VALUE_VND, _JOURNAL_FILE


# ── Đọc & ghép cặp lệnh ───────────────────────────────────────────────────────

def _trading_minutes(t0, t1) -> int:
    """Số PHÚT GIAO DỊCH giữa hai mốc — trừ giờ nghỉ trưa 11:30–13:00.

    Phải trừ, vì luật giữ lệnh đếm theo NẾN 1 phút mà phiên nghỉ trưa không
    sinh nến nào. Đếm bằng đồng hồ cho ra những dòng vô lý như "giữ 120 phút"
    trong khi giới hạn là 30 nến — người đọc tưởng bot chạy sai luật.
    """
    mins = int((t1 - t0).total_seconds() // 60)
    if mins <= 0:
        return max(mins, 0)
    # Mỗi ngày bị bắc qua nghỉ trưa thì bớt 90 phút
    _b0 = t0.normalize() + pd.Timedelta(hours=11, minutes=30)
    _b1 = t0.normalize() + pd.Timedelta(hours=13)
    if t0 < _b1 and t1 > _b0:
        mins -= int((min(t1, _b1) - max(t0, _b0)).total_seconds() // 60)
    return max(mins, 0)


def _pair_by_time(g: "pd.DataFrame") -> list:
    """Ghép MỞ↔ĐÓNG trong một nhóm, theo thứ tự thời gian. Trả [(mở, đóng), ...].

    Dòng MỞ không có ĐÓNG theo sau ⇒ lệnh chưa đóng, bỏ qua.
    Dòng ĐÓNG không có MỞ trước đó ⇒ vẫn tính (PnL nằm trên dòng ĐÓNG), chỉ
    thiếu giá vào — tốt hơn là vứt cả lệnh khỏi báo cáo lãi/lỗ.
    """
    out, pending = [], None
    for _, r in g.sort_values("_dt").iterrows():
        act = str(r.get("action", ""))
        if act.startswith("MỞ"):
            pending = r                       # MỞ mới đè MỞ cũ chưa đóng (lệnh bỏ rơi)
        elif act.startswith("ĐÓNG"):
            out.append((pending, r))
            pending = None
    return out


def load_trades(journal_file: str | None = None,
                day: date | None = None,
                day_from: date | None = None,
                day_to: date | None = None) -> pd.DataFrame:
    """Đọc journal, ghép cặp MỞ↔ĐÓNG theo trade_id, trả 1 dòng / 1 lệnh đã đóng.

    Chỉ nhận bản ghi v4 (có trade_id). Bản ghi của bot phiên bản cũ dùng cột pnl
    với ý nghĩa khác nên phải loại — xem tasks/todo.md Phase 5.
    """
    cols = ["tid", "side", "entry_time", "entry", "exit_time", "exit",
            "pnl", "net", "won", "reason", "hold_min"]
    path = journal_file or _JOURNAL_FILE
    if not path or not os.path.exists(path):
        return pd.DataFrame(columns=cols)

    try:
        df = pd.read_csv(path, on_bad_lines="skip", engine="python")
    except Exception:
        return pd.DataFrame(columns=cols)
    if df.empty or "trade_id" not in df.columns:
        return pd.DataFrame(columns=cols)

    df = df[pd.to_numeric(df["trade_id"], errors="coerce").notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["trade_id"] = pd.to_numeric(df["trade_id"]).astype(int)
    df["action"]   = df["action"].astype(str)
    df["_dt"]      = pd.to_datetime(df["time"], errors="coerce")

    # Lọc theo NGÀY ĐÓNG lệnh. Lọc trên toàn bộ bản ghi (cả MỞ lẫn ĐÓNG) theo
    # ngày sẽ cắt mất dòng MỞ của lệnh mở hôm trước — nên lọc SAU khi ghép cặp.
    _d0 = day_from or day
    _d1 = day_to   or day

    # Bỏ dòng trùng hệt nhau trước khi ghép cặp. Hai phiên Streamlit chạy song
    # song từng ghi đôi một số bản ghi (6/56 dòng trong journal thực tế).
    df = df.drop_duplicates(subset=["time", "action", "price", "trade_id"], keep="first")

    rows = []
    for tid, g in df.groupby("trade_id"):
        # Duyệt theo THỨ TỰ THỌI GIAN và ghép MỞ với ĐÓNG kế tiếp, thay vì lấy
        # `op.iloc[0]` + `cl.iloc[-1]`. Lý do: mã lệnh đã từng bị cấp trùng (hai phiên
        # trình duyệt mỗi bên một bộ đếm riêng), nên một `trade_id` có thể chứa
        # nhiều lệnh khác nhau. Cách cũ ghép giá vào của lệnh này với giá ra của
        # lệnh kia ⇒ PnL và thời gian giữ sai hoàn toàn.
        for o, c in _pair_by_time(g):
            try:
                pnl = float(c["pnl"])
            except (TypeError, ValueError):
                continue
            side = "LONG" if "LONG" in c["action"] else "SHORT"
            entry_px = float(o["price"]) if o is not None else float("nan")
            hold = ""
            if o is not None and pd.notna(o["_dt"]) and pd.notna(c["_dt"]):
                hold = _trading_minutes(o["_dt"], c["_dt"])
            rows.append({
                "tid": tid, "side": side,
                "entry_time": o["_dt"] if o is not None else pd.NaT,
                "entry": entry_px,
                "exit_time": c["_dt"], "exit": float(c["price"]),
                "pnl": pnl, "net": pnl - _FEE_PTS, "won": (pnl - _FEE_PTS) > 0,
                "reason": str(c.get("reason", "")).split("·")[0].strip(),
                "hold_min": hold,
            })
    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        return out
    if _d0 is not None:
        out = out[out["exit_time"].dt.date >= _d0]
    if _d1 is not None:
        out = out[out["exit_time"].dt.date <= _d1]
    return out.sort_values("exit_time").reset_index(drop=True)


# ── Tổng hợp ──────────────────────────────────────────────────────────────────

def summarize(trades: pd.DataFrame) -> dict:
    """Thống kê tổng hợp. Mọi giá trị tiền đã quy ra VND cho 1 hợp đồng."""
    if trades.empty:
        return {"n": 0}

    net  = trades["net"]
    wins = trades[trades["won"]]
    loss = trades[~trades["won"]]
    equity = net.cumsum()
    peak   = equity.cummax()
    gross_win  = wins["net"].sum()
    gross_loss = abs(loss["net"].sum())

    best  = trades.loc[net.idxmax()]
    worst = trades.loc[net.idxmin()]

    by_side = {}
    for s in ("LONG", "SHORT"):
        g = trades[trades["side"] == s]
        if len(g):
            by_side[s] = {"n": len(g), "net": g["net"].sum(),
                          "win_rate": g["won"].mean() * 100,
                          "avg": g["net"].mean()}

    by_reason = {}
    for r, g in trades.groupby("reason"):
        by_reason[r] = {"n": len(g), "net": g["net"].sum(), "avg": g["net"].mean()}

    return {
        "n":          len(trades),
        "wins":       len(wins),
        "losses":     len(loss),
        "win_rate":   len(wins) / len(trades) * 100,
        "pnl_gross":  trades["pnl"].sum(),
        "fee_total":  len(trades) * _FEE_PTS,
        "pnl_net":    net.sum(),
        "vnd_net":    net.sum() * _PT_VALUE_VND,
        "avg_net":    net.mean(),
        "best":       best,
        "worst":      worst,
        "max_dd":     float((equity - peak).min()) if len(equity) else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_hold":   pd.to_numeric(trades["hold_min"], errors="coerce").mean(),
        "by_side":    by_side,
        "by_reason":  by_reason,
        "equity":     equity.tolist(),
    }


# ── Tổng hợp theo kỳ (ngày / tuần / tháng) ───────────────────────────────────

_FREQ_LABEL = {"D": "Ngày", "W": "Tuần", "M": "Tháng"}


def _period_label(per, freq: str) -> str:
    """Nhãn kỳ dễ đọc cho người Việt."""
    if freq == "D":
        return per.start_time.strftime("%d/%m/%Y")
    if freq == "W":
        return (f"Tuần {per.week}/{per.year} "
                f"({per.start_time:%d/%m}–{per.end_time:%d/%m})")
    return f"Tháng {per.month:02d}/{per.year}"


def summarize_by_period(trades: pd.DataFrame, freq: str = "W") -> pd.DataFrame:
    """Gom lệnh theo kỳ. freq: "D" ngày | "W" tuần | "M" tháng.

    Gom theo NGÀY ĐÓNG lệnh — lãi/lỗ chỉ xác định khi lệnh đóng, nên đó mới là
    thời điểm đúng để quy về kỳ.
    """
    cols = ["ky", "n", "thang", "thua", "win_rate", "pnl_gross",
            "phi", "pnl_net", "vnd", "avg", "_sort"]
    if trades.empty:
        return pd.DataFrame(columns=cols)

    t = trades.copy()
    t["_per"] = pd.to_datetime(t["exit_time"]).dt.to_period(freq)

    rows = []
    for per, g in t.groupby("_per", sort=True):
        w = int(g["won"].sum())
        rows.append({
            "ky":        _period_label(per, freq),
            "n":         len(g),
            "thang":     w,
            "thua":      len(g) - w,
            "win_rate":  w / len(g) * 100,
            "pnl_gross": g["pnl"].sum(),
            "phi":       len(g) * _FEE_PTS,
            "pnl_net":   g["net"].sum(),
            "vnd":       g["net"].sum() * _PT_VALUE_VND,
            "avg":       g["net"].mean(),
            "_sort":     per.start_time,
        })
    return pd.DataFrame(rows, columns=cols).sort_values("_sort").reset_index(drop=True)


# ── Dựng HTML email ───────────────────────────────────────────────────────────
# Dùng inline style + nền sáng: mail client không đọc <style> ngoài một cách
# đáng tin, và không có dark-mode nhất quán.

_POS = "#1a7f37"   # xanh lãi
_NEG = "#c1121f"   # đỏ lỗ
_INK = "#1a1a1a"
_SUB = "#555555"


def _money(pts: float) -> str:
    return f"{fmt_vn(pts, 1, signed=True)}đ ({fmt_vn(pts * _PT_VALUE_VND, 0, signed=True)} VND)"


def build_email_html(trades: pd.DataFrame, s: dict, day: date) -> str:
    """Giữ tương thích: báo cáo 1 ngày, không kèm bảng theo kỳ."""
    return build_report_html(trades, s, day.strftime("%d/%m/%Y"))


def _period_table_html(bp: pd.DataFrame, freq: str) -> str:
    """Bảng tổng hợp theo kỳ, chèn vào báo cáo."""
    if bp.empty:
        return ""
    rows = ""
    for _, r in bp.iterrows():
        c = _POS if r["pnl_net"] > 0 else _NEG
        rows += (
            f'<tr>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{r["ky"]}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{fmt_vn(r["n"])}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_SUB}">'
            f'{fmt_vn(r["thang"])} / {fmt_vn(r["thua"])}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{fmt_vn(r["win_rate"],1)}%</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{c};font-weight:bold">'
            f'{fmt_vn(r["pnl_net"],1,signed=True)}đ</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{c};font-weight:bold">'
            f'{fmt_vn(r["vnd"],0,signed=True)}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_SUB}">'
            f'{fmt_vn(r["avg"],2,signed=True)}đ</td></tr>'
        )
    return (
        f'<h3 style="color:{_INK};margin:18px 0 6px 0;font-size:15px">'
        f'Tổng hợp theo {_FREQ_LABEL.get(freq, "kỳ").lower()}</h3>'
        f'<table style="border-collapse:collapse;width:100%;font-size:13px">'
        f'<tr style="background:#f6f8fa">'
        f'<th style="padding:7px 9px;text-align:left;color:{_SUB}">Kỳ</th>'
        f'<th style="padding:7px 9px;text-align:left;color:{_SUB}">Số lệnh</th>'
        f'<th style="padding:7px 9px;text-align:left;color:{_SUB}">T/Th</th>'
        f'<th style="padding:7px 9px;text-align:left;color:{_SUB}">Tỷ lệ thắng</th>'
        f'<th style="padding:7px 9px;text-align:left;color:{_SUB}">PnL ròng</th>'
        f'<th style="padding:7px 9px;text-align:left;color:{_SUB}">VND/HĐ</th>'
        f'<th style="padding:7px 9px;text-align:left;color:{_SUB}">TB/lệnh</th></tr>'
        f'{rows}</table>'
    )


def build_report_html(trades: pd.DataFrame, s: dict, label: str,
                      by_period: pd.DataFrame | None = None,
                      freq: str = "W") -> str:
    day_str = label
    if not s.get("n"):
        return (f'<div style="font-family:Arial,sans-serif;color:{_INK};padding:16px">'
                f'<h2 style="color:{_INK}">Báo cáo VN30F1M — {day_str}</h2>'
                f'<p style="color:{_SUB}">Không có lệnh nào được đóng trong kỳ.</p></div>')

    clr   = _POS if s["pnl_net"] > 0 else _NEG
    verdict = "LÃI" if s["pnl_net"] > 0 else "LỖ"

    rows = ""
    for _, t in trades.iterrows():
        c = _POS if t["won"] else _NEG
        rows += (
            f'<tr>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_SUB}">#{t["tid"]}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_INK};font-weight:bold">{t["side"]}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">'
            f'{t["entry_time"].strftime("%H:%M") if pd.notna(t["entry_time"]) else "—"} · {fmt_vn(t["entry"],1)}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">'
            f'{t["exit_time"].strftime("%H:%M") if pd.notna(t["exit_time"]) else "—"} · {fmt_vn(t["exit"],1)}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_SUB}">'
            f'{fmt_vn(t["hold_min"],0) if t["hold_min"] != "" else "—"}\'</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{c};font-weight:bold">'
            f'{fmt_vn(t["net"],1,signed=True)}đ</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{c};font-weight:bold">'
            f'{"THẮNG" if t["won"] else "THUA"}</td>'
            f'<td style="padding:7px 9px;border-bottom:1px solid #e5e5e5;color:{_SUB};font-size:12px">{t["reason"]}</td>'
            f'</tr>'
        )

    def _kv(label, value, color=_INK, sub=""):
        return (f'<td style="padding:10px 14px;background:#f6f8fa;border:1px solid #e5e5e5;'
                f'border-radius:6px;color:{_INK}">'
                f'<div style="font-size:12px;color:{_SUB}">{label}</div>'
                f'<div style="font-size:19px;font-weight:bold;color:{color}">{value}</div>'
                + (f'<div style="font-size:11px;color:{_SUB}">{sub}</div>' if sub else "")
                + '</td>')

    side_rows = ""
    for sd, v in s["by_side"].items():
        c = _POS if v["net"] > 0 else _NEG
        side_rows += (
            f'<tr><td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{sd}</td>'
            f'<td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{fmt_vn(v["n"])}</td>'
            f'<td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{fmt_vn(v["win_rate"],1)}%</td>'
            f'<td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{c};font-weight:bold">'
            f'{fmt_vn(v["net"],1,signed=True)}đ</td>'
            f'<td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{_SUB}">{fmt_vn(v["avg"],2,signed=True)}đ</td></tr>'
        )

    reason_rows = ""
    for r, v in sorted(s["by_reason"].items(), key=lambda x: -x[1]["net"]):
        c = _POS if v["net"] > 0 else _NEG
        reason_rows += (
            f'<tr><td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{r}</td>'
            f'<td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{_INK}">{fmt_vn(v["n"])}</td>'
            f'<td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{c};font-weight:bold">'
            f'{fmt_vn(v["net"],1,signed=True)}đ</td>'
            f'<td style="padding:6px 9px;border-bottom:1px solid #e5e5e5;color:{_SUB}">{fmt_vn(v["avg"],2,signed=True)}đ</td></tr>'
        )

    pf = "∞" if s["profit_factor"] == float("inf") else fmt_vn(s["profit_factor"], 2)

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;color:{_INK};background:#ffffff;
padding:18px;max-width:900px">
  <h2 style="color:{_INK};margin:0 0 4px 0">Báo cáo lãi/lỗ VN30F1M — {day_str}</h2>
  <div style="color:{_SUB};font-size:13px;margin-bottom:14px">
    Bot tín hiệu phái sinh v4 · phí giả định {fmt_vn(_FEE_PTS,2)}đ/lệnh (đã gồm spread + slippage)
    · 1 điểm = {fmt_vn(_PT_VALUE_VND)} VND/hợp đồng
  </div>

  <div style="background:{'#eaf6ec' if s['pnl_net'] > 0 else '#fdeaea'};border:2px solid {clr};
       border-radius:8px;padding:14px 18px;margin-bottom:16px;color:{_INK}">
    <div style="font-size:13px;color:{_SUB}">Kết quả ròng cả ngày</div>
    <div style="font-size:28px;font-weight:bold;color:{clr}">
      {verdict} {fmt_vn(s['pnl_net'],1,signed=True)}đ
      <span style="font-size:17px">= {fmt_vn(s['vnd_net'],0,signed=True)} VND/hợp đồng</span>
    </div>
    <div style="font-size:12px;color:{_SUB}">
      {fmt_vn(s['n'])} lệnh · thắng {fmt_vn(s['wins'])} / thua {fmt_vn(s['losses'])}
      · tỷ lệ thắng {fmt_vn(s['win_rate'],1)}%
    </div>
  </div>

  <table style="border-collapse:separate;border-spacing:6px;width:100%;margin-bottom:6px"><tr>
    {_kv("PnL gộp", fmt_vn(s['pnl_gross'],1,signed=True) + "đ")}
    {_kv("Tổng phí", "−" + fmt_vn(s['fee_total'],2) + "đ", _NEG, f"{fmt_vn(s['n'])} lệnh")}
    {_kv("TB mỗi lệnh", fmt_vn(s['avg_net'],2,signed=True) + "đ", _POS if s['avg_net']>0 else _NEG)}
    {_kv("Profit factor", pf, _POS if s['profit_factor']>=1 else _NEG, "lãi/lỗ")}
  </tr><tr>
    {_kv("Sụt giảm tối đa", fmt_vn(s['max_dd'],1) + "đ", _NEG, "trong ngày")}
    {_kv("Giữ lệnh TB", fmt_vn(s['avg_hold'],0) + " phút")}
    {_kv("Lệnh lãi nhất", f"#{s['best']['tid']} {fmt_vn(s['best']['net'],1,signed=True)}đ", _POS,
         str(s['best']['side']))}
    {_kv("Lệnh lỗ nhất", f"#{s['worst']['tid']} {fmt_vn(s['worst']['net'],1,signed=True)}đ", _NEG,
         str(s['worst']['side']))}
  </tr></table>

  <h3 style="color:{_INK};margin:18px 0 6px 0;font-size:15px">Theo chiều lệnh</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr style="background:#f6f8fa">
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Chiều</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Số lệnh</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Tỷ lệ thắng</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">PnL ròng</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">TB/lệnh</th></tr>
    {side_rows}
  </table>

  <h3 style="color:{_INK};margin:18px 0 6px 0;font-size:15px">Theo lý do thoát</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr style="background:#f6f8fa">
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Lý do</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Số lệnh</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">PnL ròng</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">TB/lệnh</th></tr>
    {reason_rows}
  </table>

  {_period_table_html(by_period, freq) if by_period is not None else ""}

  <h3 style="color:{_INK};margin:18px 0 6px 0;font-size:15px">Chi tiết từng lệnh</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr style="background:#f6f8fa">
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Mã</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Chiều</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Vào</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Ra</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Giữ</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">PnL ròng</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Kết quả</th>
      <th style="padding:7px 9px;text-align:left;color:{_SUB}">Lý do thoát</th></tr>
    {rows}
  </table>

  <p style="color:{_SUB};font-size:11.5px;margin-top:18px;line-height:1.55">
    PnL ròng đã trừ phí {fmt_vn(_FEE_PTS,2)}đ/lệnh, tính cho <b>1 hợp đồng</b>.
    Đây là kết quả mô phỏng của bot theo tín hiệu, không phải sao kê tài khoản thật —
    giá vào lệnh lấy tại giá đóng của nến đã đóng nên có thể lệch so với khớp lệnh thực tế.
  </p>
</div>"""


# ── Gửi email ─────────────────────────────────────────────────────────────────

def email_config() -> dict:
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587") or 587),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "to": os.getenv("REPORT_EMAIL_TO", ""),
        "from": os.getenv("REPORT_EMAIL_FROM", "") or os.getenv("SMTP_USER", ""),
    }


def email_ready() -> tuple[bool, str]:
    c = email_config()
    missing = [k for k in ("user", "password", "to") if not c[k]]
    if missing:
        names = {"user": "SMTP_USER", "password": "SMTP_PASSWORD", "to": "REPORT_EMAIL_TO"}
        return False, "Thiếu trong .env: " + ", ".join(names[m] for m in missing)
    return True, f"Gửi tới {c['to']} qua {c['host']}:{c['port']}"


def send_report_email(html: str, subject: str,
                      to_addr: str | None = None) -> tuple[bool, str]:
    ok, msg = email_ready()
    if not ok:
        return False, msg
    c = email_config()
    recipients = [a.strip() for a in (to_addr or c["to"]).split(",") if a.strip()]

    m = EmailMessage()
    m["Subject"] = subject
    m["From"]    = c["from"]
    m["To"]      = ", ".join(recipients)
    m.set_content("Báo cáo dạng HTML — vui lòng mở bằng trình đọc hỗ trợ HTML.")
    m.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=30) as srv:
            srv.starttls()
            srv.login(c["user"], c["password"])
            srv.send_message(m)
        return True, f"Đã gửi tới {', '.join(recipients)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def build_report(day: date | None = None,
                 journal_file: str | None = None) -> tuple[pd.DataFrame, dict, str]:
    """Tiện ích một bước cho báo cáo 1 NGÀY: trả (trades, summary, html)."""
    d = day or date.today()
    trades = load_trades(journal_file, d)
    s = summarize(trades)
    return trades, s, build_email_html(trades, s, d)


def build_range_report(day_from: date, day_to: date, freq: str = "W",
                       journal_file: str | None = None
                       ) -> tuple[pd.DataFrame, dict, pd.DataFrame, str]:
    """Báo cáo cho KHOẢNG ngày, kèm bảng tổng hợp theo kỳ.

    Trả (trades, summary, by_period, html). freq: "D" | "W" | "M".
    """
    trades = load_trades(journal_file, day_from=day_from, day_to=day_to)
    s      = summarize(trades)
    bp     = summarize_by_period(trades, freq)
    label  = (f"{day_from:%d/%m/%Y}" if day_from == day_to
              else f"{day_from:%d/%m/%Y} – {day_to:%d/%m/%Y}")
    html   = build_report_html(trades, s, label, bp, freq)
    return trades, s, bp, html
