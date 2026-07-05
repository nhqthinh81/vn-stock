"""
Pre-Trade Analysis Panel — phân tích toàn diện trước khi vào lệnh.

Gọi qua render_panel(symbol, row_data) từ tab_scan.py.

Scoring theo phong cách đầu tư:
  Ngắn hạn (T+3~5):   KT=55 / Fund=10 / Macro=10 / Ami=15 / News=10
  Trung hạn (T+20):   KT=40 / Fund=25 / Macro=15 / Ami=10 / News=10
  Dài hạn  (≥3 tháng): KT=20 / Fund=45 / Macro=20 / Ami=5  / News=10

News là modifier độc lập: -15 → +12 điểm thêm vào tổng.
Verdict: MUA ✅ (≥65) / CHỜ 🟡 (45-64) / TRÁNH 🔴 (<45)
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import streamlit as st

_APP_DIR = Path(__file__).parent.parent

# ── Trọng số theo phong cách đầu tư ──────────────────────────────────────────
_STYLES = {
    "⚡ Ngắn hạn (T+3~5)":    dict(tech=55, fund=10, macro=10, ami=15, news=10),
    "📈 Trung hạn (T+20)":    dict(tech=40, fund=25, macro=15, ami=10, news=10),
    "🏦 Dài hạn (≥3 tháng)":  dict(tech=20, fund=45, macro=20, ami=5,  news=10),
}

# ── Từ khóa phân loại tin tức ─────────────────────────────────────────────────
_KW_POSITIVE = [
    "tăng trưởng", "lợi nhuận tăng", "doanh thu tăng", "vượt kế hoạch",
    "kỷ lục", "tích cực", "khả quan", "phục hồi", "hợp đồng lớn",
    "ký kết", "mở rộng", "thị phần", "cổ tức cao", "chia thưởng",
    "mua vào", "nâng cấp", "tăng vốn thành công", "xuất khẩu tăng",
    "đơn hàng", "backlog", "lợi nhuận kỷ lục", "kết quả tốt",
    "vượt", "tăng mạnh", "bứt phá", "đột phá",
]
_KW_NEGATIVE = [
    "thua lỗ", "lỗ", "sụt giảm", "giảm mạnh", "vi phạm", "bị phạt",
    "điều tra", "khởi tố", "kiện", "từ chức", "cảnh báo sàn",
    "hủy niêm yết", "tạm ngừng", "nợ xấu", "mất khả năng", "chậm trả",
    "giảm cổ tức", "cắt giảm", "thu hẹp", "đóng cửa", "phát hành pha loãng",
    "pha loãng", "chào bán riêng lẻ", "phát hành thêm",
    "doanh thu giảm", "lợi nhuận giảm", "dưới kế hoạch",
]

# Tin trọng yếu — ảnh hưởng trực tiếp đến giá trị nội tại
_KW_MATERIAL = {
    "kqkd":         ["kqkd", "kết quả kinh doanh", "báo cáo tài chính", "lợi nhuận quý", "lợi nhuận năm"],
    "ma":           ["mua lại", "sáp nhập", "m&a", "thâu tóm", "chuyển nhượng cổ phần lớn", "thâu tóm"],
    "leadership":   ["tổng giám đốc", "chủ tịch hđqt", "từ chức", "bổ nhiệm", "thay đổi lãnh đạo", "hđqt"],
    "capital":      ["phát hành", "tăng vốn", "phát hành thêm", "chào bán", "esop", "trái phiếu"],
    "legal":        ["điều tra", "khởi tố", "tố cáo", "vi phạm pháp luật", "bị phạt", "kiện tụng"],
    "dividend":     ["cổ tức", "chia cổ tức", "cổ phiếu thưởng", "tỷ lệ cổ tức"],
    "listing":      ["hủy niêm yết", "cảnh báo", "kiểm soát", "tạm ngừng giao dịch"],
    "contract":     ["hợp đồng", "ký kết", "dự án lớn", "thắng thầu", "trúng thầu"],
}
_MATERIAL_LABEL = {
    "kqkd":       "📊 KQKD",
    "ma":         "🤝 M&A",
    "leadership": "👤 Lãnh đạo",
    "capital":    "💰 Vốn/TP",
    "legal":      "⚖️ Pháp lý",
    "dividend":   "💵 Cổ tức",
    "listing":    "⛔ Niêm yết",
    "contract":   "📝 Hợp đồng",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default


def _color_score(score: float) -> str:
    if score >= 65: return "#4caf50"
    if score >= 45: return "#ff9800"
    return "#f44336"


def _verdict(score: float) -> tuple[str, str]:
    if score >= 65: return "✅ MUA",  "#4caf50"
    if score >= 45: return "🟡 CHỜ", "#ff9800"
    return "🔴 TRÁNH", "#f44336"


def _text_contains(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw in t for kw in keywords)


def _detect_material_type(text: str) -> Optional[str]:
    t = text.lower()
    for mtype, kws in _KW_MATERIAL.items():
        if any(kw in t for kw in kws):
            return mtype
    return None


# ── Section 1: Kỹ thuật ───────────────────────────────────────────────────────

def _score_technical(row: dict, max_pts: int) -> tuple[float, list[str], list[str]]:
    pros: list[str] = []
    cons: list[str] = []
    raw = 0.0

    tech_score = _safe(row.get("tech_score"), 50)
    rsi        = _safe(row.get("rsi"), 50)
    dist       = _safe(row.get("dist_ema34_pct"), 0)
    signal     = row.get("signal", "HOLD") or "HOLD"
    risk       = row.get("risk", "Medium") or "Medium"
    phase      = row.get("phase", "Neutral") or "Neutral"
    wmt        = int(_safe(row.get("weekly_macd_trend"), 0))
    ma_al      = int(_safe(row.get("ma_aligned"), 0))
    vol_ratio  = _safe(row.get("volume_ratio"), 1.0)
    is_star    = signal == "BUY-A*"

    # tech_score → 0-50
    raw += (tech_score / 100) * 50

    if tech_score >= 70: pros.append(f"Tech score cao ({tech_score:.0f}/100)")
    elif tech_score <= 35: cons.append(f"Tech score yếu ({tech_score:.0f}/100)")

    # Signal → 0-20
    sig_raw = {"BUY-A*": 20, "BUY-A": 18, "BUY-B": 13, "HOLD": 7, "SELL-B": 3, "SELL-A": 0}
    raw += sig_raw.get(signal, 7)
    if signal in ("BUY-A*", "BUY-A"):
        pros.append(f"Tín hiệu {signal}" + (" ⭐ ngành top + RSI≥60" if is_star else ""))
    elif signal in ("SELL-A", "SELL-B"):
        cons.append(f"Tín hiệu {signal}")

    # RSI → 0-10
    if 40 <= rsi <= 65:
        raw += 10; pros.append(f"RSI lý tưởng ({rsi:.1f})")
    elif rsi < 30:
        raw += 8;  pros.append(f"RSI oversold ({rsi:.1f}) — cơ hội đảo chiều")
    elif rsi > 75:
        cons.append(f"RSI overbought ({rsi:.1f}) — rủi ro pullback")

    # Phase → 0-10
    phase_pts = {"Accumulation": 10, "Markup": 10, "Neutral": 5, "Distribution": 0, "Markdown": 0}
    raw += phase_pts.get(phase, 5)
    if phase in ("Accumulation", "Markup"): pros.append(f"Giai đoạn {phase}")
    elif phase in ("Distribution", "Markdown"): cons.append(f"Giai đoạn {phase}")

    # Risk → 0-5
    raw += {"Low": 5, "Medium": 3, "High": 0}.get(risk, 3)
    if risk == "High": cons.append("Rủi ro kỹ thuật cao (High)")
    elif risk == "Low": pros.append("Rủi ro thấp (Low)")

    # Trend → 0-5
    if wmt >= 0 and ma_al > 0:
        raw += 5; pros.append("Weekly MACD + MA aligned tích cực")
    elif wmt < 0 or ma_al < 0:
        cons.append("Xu hướng tuần hoặc MA không thuận chiều")

    # Volume bonus
    if vol_ratio >= 1.5:
        raw = min(100, raw + 3); pros.append(f"Khối lượng xác nhận (×{vol_ratio:.1f})")

    # Dist cảnh báo
    if dist > 15:
        cons.append(f"Xa EMA34 quá nhiều (+{dist:.1f}%) — rủi ro pullback")
    elif dist < -15:
        cons.append(f"Dưới EMA34 sâu ({dist:.1f}%)")

    # Scale về max_pts
    score = (min(raw, 100) / 100) * max_pts
    return score, pros, cons


# ── Section 2: Cơ bản ────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_fundamental(symbol: str) -> dict:
    """
    Lấy BCTC quý từ KBS (data mới nhất ~3 tháng).
    KBS trả long format: row=metric (item_id), col=kỳ báo cáo (newest first).
    Tất cả giá trị % đã ở dạng phần trăm (roe=8.5 → 8.5%), không cần nhân 100.
    D/E từ KBS ở dạng % (126.6 = 1.266x) → chia 100 để ra ratio.
    """
    try:
        from vnstock import Finance
        fin = Finance(symbol=symbol, source="KBS")
        df = fin.ratio(period="quarter", lang="en", dropna=False)

        if df is None or df.empty:
            return {}

        # Cột date = tất cả cột trừ 'item', 'item_en', 'item_id' (newest first)
        meta_cols = {"item", "item_en", "item_id"}
        date_cols = [c for c in df.columns if c not in meta_cols]
        if not date_cols:
            return {}

        def _get_metric(item_id: str):
            """Lấy giá trị mới nhất (non-NaN) của 1 metric."""
            rows = df[df["item_id"] == item_id]
            if rows.empty:
                return None
            row = rows.iloc[0]
            for col in date_cols:
                v = row[col]
                try:
                    f = float(v)
                    if not math.isnan(f):
                        return f
                except (TypeError, ValueError):
                    pass
            return None

        # Ưu tiên ROE trailing (TTM), fallback về quarterly ROE
        roe = _get_metric("roe_trailling") or _get_metric("roe")

        # ROE growth: so với cùng kỳ năm trước (4 quý trước = date_cols[4] nếu có)
        roe_growth = None
        if len(date_cols) >= 5:
            roe_rows = df[df["item_id"].isin(["roe_trailling", "roe"])]
            if not roe_rows.empty:
                row_r = roe_rows.iloc[0]
                def _v(col):
                    try:
                        f = float(row_r[col])
                        return None if math.isnan(f) else f
                    except Exception:
                        return None
                r_now  = _v(date_cols[0])
                r_prev = _v(date_cols[4])
                if r_now is not None and r_prev and r_prev != 0:
                    roe_growth = (r_now - r_prev) / abs(r_prev) * 100

        pe  = _get_metric("pe_ratio")
        pb  = _get_metric("pb_ratio")
        npm = _get_metric("net_margin")
        eps = _get_metric("trailing_eps")

        # D/E từ KBS dạng % (126.6 = 1.266x) → chia 100
        dte_raw = _get_metric("debt_to_equity")
        dte = dte_raw / 100.0 if dte_raw is not None else None

        period_lbl = date_cols[0]  # e.g. "2026-Q1"

        return dict(pe=pe, pb=pb, roe=roe, npm=npm, dte=dte, eps=eps,
                    roe_growth=roe_growth,
                    report_period=period_lbl,
                    is_quarterly=True)
    except Exception:
        return {}


def _score_fundamental(fund: dict, max_pts: int) -> tuple[float, list[str], list[str]]:
    pros: list[str] = []
    cons: list[str] = []

    if not fund:
        return max_pts * 0.45, [], ["Không lấy được dữ liệu BCTC từ KBS — chưa đánh giá được cơ bản"]

    raw = 0.0

    pe  = fund.get("pe")
    roe = fund.get("roe")
    npm = fund.get("npm")
    dte = fund.get("dte")
    eps = fund.get("eps")
    roe_growth  = fund.get("roe_growth")
    # P/E → 0-35 (định giá: trả bao nhiêu cho 1 đồng lợi nhuận)
    if pe is not None:
        if pe <= 0:
            raw += 0;  cons.append(f"P/E âm ({pe:.1f}x) — doanh nghiệp đang thua lỗ")
        elif pe <= 12:
            raw += 35; pros.append(f"P/E hấp dẫn {pe:.1f}x — định giá rẻ, thị trường chưa phản ánh đủ giá trị")
        elif pe <= 20:
            raw += 25; pros.append(f"P/E hợp lý {pe:.1f}x — định giá cân bằng với tăng trưởng")
        elif pe <= 30:
            raw += 12; cons.append(f"P/E cao {pe:.1f}x — giá đã phản ánh kỳ vọng tăng trưởng mạnh, ít dư địa")
        else:
            raw += 0;  cons.append(f"P/E rất cao {pe:.1f}x — định giá premium, rủi ro nếu tăng trưởng chậm lại")
    else:
        raw += 15; cons.append("Không có dữ liệu P/E — không đánh giá được định giá")

    # ROE → 0-35 (hiệu quả sử dụng vốn chủ sở hữu)
    # KBS trả về % sẵn (8.5 = 8.5%), không nhân thêm 100
    if roe is not None:
        roe_pct = roe
        if roe_pct >= 20:
            raw += 35; pros.append(f"ROE xuất sắc {roe_pct:.1f}% — tạo giá trị vượt trội cho cổ đông (ngưỡng tốt ≥20%)")
        elif roe_pct >= 15:
            raw += 25; pros.append(f"ROE tốt {roe_pct:.1f}% — hiệu quả sử dụng vốn khá, trên mức trung bình thị trường")
        elif roe_pct >= 10:
            raw += 15; cons.append(f"ROE trung bình {roe_pct:.1f}% — hiệu quả vốn ở mức chấp nhận được, chưa nổi bật")
        else:
            raw += 5;  cons.append(f"ROE thấp {roe_pct:.1f}% — hiệu quả vốn kém, dấu hiệu kinh doanh gặp khó")
        if roe_growth and roe_growth > 5:
            raw = min(100, raw + 5)
            pros.append(f"ROE cải thiện +{roe_growth:.1f}% so cùng kỳ — xu hướng sinh lời đang tốt lên")
        elif roe_growth and roe_growth < -10:
            cons.append(f"ROE suy giảm {roe_growth:.1f}% so cùng kỳ — hiệu quả kinh doanh đang xấu đi")
    else:
        raw += 15; cons.append("Không có dữ liệu ROE — không đánh giá được hiệu quả vốn")

    # NPM → 0-20 (biên lợi nhuận ròng — sức mạnh sinh lời thực)
    # KBS trả về % sẵn (23.96 = 23.96%), không nhân thêm 100
    if npm is not None:
        npm_pct = npm
        if npm_pct >= 15:
            raw += 20; pros.append(f"Biên lãi ròng {npm_pct:.1f}% — rất cao, doanh nghiệp giữ lại tốt từ doanh thu")
        elif npm_pct >= 8:
            raw += 13; pros.append(f"Biên lãi ròng {npm_pct:.1f}% — ở mức tốt, kiểm soát chi phí hiệu quả")
        elif npm_pct >= 3:
            raw += 5;  cons.append(f"Biên lãi ròng mỏng {npm_pct:.1f}% — lợi nhuận dễ bị ăn mòn khi chi phí tăng")
        elif npm_pct < 0:
            cons.append(f"Biên lãi ròng âm {npm_pct:.1f}% — đang lỗ ở mức hoạt động")
        else:
            raw += 2;  cons.append(f"Biên lãi ròng rất mỏng {npm_pct:.1f}% — rủi ro cao với biến động chi phí")
    else:
        raw += 8; cons.append("Không có dữ liệu NPM — không đánh giá được biên lợi nhuận")

    # D/E → 0-10 (đòn bẩy tài chính)
    if dte is not None:
        if dte <= 0.5:
            raw += 10; pros.append(f"Nợ/Vốn thấp {dte:.2f}x — bảng cân đối lành mạnh, ít phụ thuộc vay nợ")
        elif dte <= 1.5:
            raw += 7;  pros.append(f"Nợ/Vốn ở mức chấp nhận được {dte:.2f}x — đòn bẩy vừa phải")
        elif dte <= 3.0:
            raw += 3;  cons.append(f"Nợ/Vốn cao {dte:.1f}x — áp lực lãi vay lớn, dễ rủi ro khi lãi suất tăng")
        else:
            cons.append(f"Nợ/Vốn rất cao {dte:.1f}x — đòn bẩy quá mức, nguy cơ tài chính đáng kể")
    else:
        raw += 5; cons.append("Không có dữ liệu D/E — không đánh giá được đòn bẩy tài chính")

    # EPS — chỉ hiển thị thông tin bổ sung, không tính điểm riêng
    if eps is not None and eps > 0:
        pros.append(f"EPS {eps:,.0f} đ/cp — lợi nhuận trên mỗi cổ phiếu dương")
    elif eps is not None and eps <= 0:
        cons.append(f"EPS âm ({eps:,.0f} đ/cp) — chưa tạo ra lợi nhuận cho cổ đông")

    score = (min(raw, 100) / 100) * max_pts
    return score, pros, cons


# ── Section 3: Vĩ mô ─────────────────────────────────────────────────────────

def _score_macro(max_pts: int) -> tuple[float, list[str], list[str], dict, str]:
    """Trả về (score, pros, cons, key_data, cache_timestamp)."""
    pros: list[str] = []
    cons: list[str] = []
    key_data: dict  = {}
    cache_ts: str   = ""

    cache_path = _APP_DIR / "data" / "macro_cache.json"
    if not cache_path.exists():
        return max_pts * 0.5, ["⚠️ Chưa có macro_cache.json — chạy update_macro.py"], [], {}, ""

    try:
        import json
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return max_pts * 0.5, [], ["Lỗi đọc macro_cache.json"], {}, ""

    cache_ts = cache.get("generated_at", "")[:16]
    groups   = cache.get("groups", {})
    raw      = 0.0

    def _sig(gk, ik): return groups.get(gk, {}).get(ik, {}).get("signal")
    def _val(gk, ik):
        v = groups.get(gk, {}).get(ik, {}).get("value")
        try: return float(v)
        except: return None

    # CPI → 0-25
    cpi_val = _val("group1_real_economy", "cpi_yoy_pct")
    cpi_sig = _sig("group1_real_economy", "cpi_yoy_pct")
    if cpi_val is not None: key_data["CPI YoY"] = f"{cpi_val:.1f}%"
    if cpi_sig == "TÍCH":  raw += 25; pros.append(f"CPI ổn định ({cpi_val:.1f}%)")
    elif cpi_sig == "TIÊU": raw += 5;  cons.append(f"Lạm phát cao ({cpi_val:.1f}%)")
    else:                   raw += 13

    # PMI → 0-25
    pmi_val = _val("group1_real_economy", "pmi_manufacturing")
    if pmi_val is not None: key_data["PMI"] = f"{pmi_val:.1f}"
    if pmi_val is not None:
        if pmi_val >= 52:   raw += 25; pros.append(f"PMI mạnh ({pmi_val:.1f})")
        elif pmi_val >= 50: raw += 17; pros.append(f"PMI trên 50 ({pmi_val:.1f})")
        elif pmi_val >= 48: raw += 7;  cons.append(f"PMI dưới 50 ({pmi_val:.1f})")
        else:               raw += 2;  cons.append(f"PMI yếu ({pmi_val:.1f})")
    else:
        raw += 13

    # Fed → 0-25
    fed_val = _val("group4_global", "fed_rate_pct")
    fed_sig = _sig("group4_global", "fed_rate_pct")
    if fed_val is not None: key_data["Fed Rate"] = f"{fed_val:.2f}%"
    if fed_sig == "TÍCH":  raw += 25; pros.append(f"Fed rate hỗ trợ ({fed_val:.2f}%)")
    elif fed_sig == "TIÊU": raw += 5;  cons.append(f"Fed rate cao ({fed_val:.2f}%) — áp lực dòng vốn")
    else:                   raw += 13

    # USD/VND → 0-25
    usd_val = _val("group2_monetary", "usd_vnd_rate")
    usd_sig = _sig("group2_monetary", "usd_vnd_rate")
    if usd_val is not None: key_data["USD/VND"] = f"{int(usd_val):,}"
    if usd_sig == "TÍCH":  raw += 25; pros.append("Tỷ giá ổn định")
    elif usd_sig == "TIÊU": raw += 5;  cons.append("VND mất giá — áp lực lạm phát nhập khẩu")
    else:                   raw += 13

    score = (min(raw, 100) / 100) * max_pts
    return score, pros, cons, key_data, cache_ts


# ── Section 4: Amibroker ──────────────────────────────────────────────────────

def _score_ami(row: dict, max_pts: int) -> tuple[float, list[str], list[str]]:
    pros: list[str] = []
    cons: list[str] = []

    ami_rec      = (row.get("ami_rec_label") or "").strip().upper()
    ami_score_raw = _safe(row.get("ami_score"), 0)  # raw Amibroker score 0-100
    ami_setup    = row.get("ami_setup", "") or ""
    ami_fore     = row.get("ami_forecast", "") or ""

    # Kết hợp ami_rec label và ami_score_raw để tính điểm
    rec_raw = {"STRONG BUY": 100, "ACCUMULATE": 80, "WATCHING": 50, "RISK SELL": 20, "TOP SELL": 0}
    raw = float(rec_raw.get(ami_rec, 50))
    # Nếu ami_score_raw hợp lệ thì blend 50/50
    if ami_score_raw > 0:
        raw = raw * 0.5 + ami_score_raw * 0.5

    if ami_rec in ("STRONG BUY", "ACCUMULATE"): pros.append(f"Ami Rec: {ami_rec}")
    elif ami_rec in ("RISK SELL", "TOP SELL"):   cons.append(f"Ami Rec: {ami_rec}")

    if ami_setup:
        pros.append(f"Setup: {ami_setup}")
    if ami_fore:
        if "BULL" in ami_fore.upper(): pros.append(f"Forecast: {ami_fore}")
        elif "BEAR" in ami_fore.upper(): cons.append(f"Forecast: {ami_fore}")

    score = (raw / 100) * max_pts
    return score, pros, cons


# ── Section 5: Market Regime (VN-Index) ──────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_market_regime() -> dict:
    """Lấy VN-Index trend gần nhất để xác định market regime."""
    try:
        from vnstock import Trading
        t  = Trading(source="KBS", symbol="VNINDEX")
        df = t.price_board(symbols_list=["VNINDEX"])
        if df is not None and not df.empty:
            close = float(df.iloc[0].get("close_price", 0) or df.iloc[0].get("match_price", 0) or 0)
            return {"close": close, "ok": True}
    except Exception:
        pass

    # Fallback: đọc từ scan_cache nếu có VNINDEX
    try:
        from vn_invest.screener import load_cache
        cache = load_cache()
        vni = next((r for r in cache if r.get("symbol") == "VNINDEX"), None)
        if vni:
            return {
                "close": vni.get("close", 0),
                "wmt":   int(_safe(vni.get("weekly_macd_trend"), 0)),
                "phase": vni.get("phase", "Neutral"),
                "ok":    True,
            }
    except Exception:
        pass
    return {"ok": False}


def _market_regime_label(regime: dict) -> tuple[str, str, int]:
    """Trả (label, color, modifier điểm -10..+5)."""
    if not regime.get("ok"):
        return "Không có dữ liệu VN-Index", "#888", 0
    wmt   = int(regime.get("wmt", 0))
    phase = regime.get("phase", "Neutral")
    if wmt > 0 and phase in ("Markup", "Accumulation"):
        return "📈 Bull — VN-Index xu hướng tăng", "#4caf50", 5
    if wmt < 0 and phase in ("Markdown", "Distribution"):
        return "📉 Bear — VN-Index xu hướng giảm", "#f44336", -10
    return "↔️ Sideway — VN-Index đi ngang", "#ff9800", 0


# ── Section 6: Tin Tức ────────────────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def _fetch_news_and_events(symbol: str) -> tuple[list[dict], list[dict]]:
    from vn_invest.data import get_company_news, get_company_events
    news   = get_company_news(symbol, source="VCI")
    events = get_company_events(symbol, source="VCI")
    return news, events


def _classify_news(title: str, summary: str = "") -> str:
    """Phân loại: POSITIVE / NEGATIVE / NEUTRAL."""
    text = (title + " " + summary).lower()
    pos  = sum(1 for kw in _KW_POSITIVE if kw in text)
    neg  = sum(1 for kw in _KW_NEGATIVE if kw in text)
    if neg > pos:   return "NEGATIVE"
    if pos > neg:   return "POSITIVE"
    return "NEUTRAL"


def _news_item_weight(title: str, summary: str, sentiment: str, material_type: Optional[str]) -> float:
    """Trả về điểm ảnh hưởng của 1 tin: -10..+8."""
    text = (title + " " + summary).lower()
    base = {"POSITIVE": 3.0, "NEUTRAL": 0.0, "NEGATIVE": -3.0}[sentiment]

    # Tin trọng yếu → nhân hệ số
    if material_type in ("kqkd", "ma", "legal", "listing"):
        base *= 2.5  # max: ±7.5
    elif material_type in ("capital", "leadership"):
        base *= 1.8  # max: ±5.4
    elif material_type in ("dividend", "contract"):
        base *= 1.4  # max: ±4.2

    # Extra signals
    if any(kw in text for kw in ["kỷ lục", "vượt kế hoạch", "lợi nhuận kỷ lục"]):
        base += 2
    if any(kw in text for kw in ["điều tra", "khởi tố", "tố cáo"]):
        base -= 4
    if any(kw in text for kw in ["hủy niêm yết", "tạm ngừng giao dịch"]):
        base -= 5

    return max(-10.0, min(8.0, base))


def _score_news(symbol: str) -> tuple[float, list[dict], list[str]]:
    """
    Trả về (modifier -15..+12, analyzed_items, warning_list).
    modifier được cộng vào total_score, không phải section riêng.
    """
    news, events = _fetch_news_and_events(symbol)
    warnings: list[str] = []
    analyzed: list[dict] = []

    # Phân tích từng tin
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    for item in news:
        date    = item.get("date", "")
        title   = item.get("title", "")
        summary = item.get("summary", "") or item.get("content", "")[:200]

        # Chỉ xem xét tin trong 60 ngày
        if date and date < cutoff:
            continue

        sentiment = _classify_news(title, summary)
        mtype     = _detect_material_type(title + " " + summary)
        weight    = _news_item_weight(title, summary, sentiment, mtype)

        analyzed.append(dict(
            date=date, title=title, summary=summary[:120],
            sentiment=sentiment, material_type=mtype, weight=weight,
        ))

    # Thêm events (corporate actions)
    for ev in events:
        title  = ev.get("title", "") or ev.get("type", "")
        date   = ev.get("date", "")
        if not title: continue
        mtype  = _detect_material_type(title)
        sent   = _classify_news(title)
        weight = _news_item_weight(title, "", sent, mtype)
        analyzed.append(dict(
            date=date, title=f"[Sự kiện] {title}", summary="",
            sentiment=sent, material_type=mtype, weight=weight, is_event=True,
        ))

    if not analyzed:
        return 0.0, [], ["Không tìm thấy tin tức trong 60 ngày gần đây"]

    # Lấy top 5 tin ảnh hưởng nhất (theo |weight|)
    top5    = sorted(analyzed, key=lambda x: abs(x["weight"]), reverse=True)[:5]
    total_w = sum(x["weight"] for x in top5)

    # Cảnh báo tin trọng yếu tiêu cực
    for item in top5:
        if item["weight"] <= -4:
            mt = _MATERIAL_LABEL.get(item.get("material_type", ""), "⚠️")
            warnings.append(f"{mt} {item['title'][:60]}")

    modifier = max(-15.0, min(12.0, total_w))
    return modifier, analyzed, warnings


# ── Target & Stop Loss ────────────────────────────────────────────────────────

def _calc_targets(price: float, total_score: float, row: dict) -> tuple[float, float]:
    atr_pct      = max(1.0, _safe(row.get("atr_pct"), 2.0))
    stop_pct     = max(5.0, atr_pct * 2.5)
    if total_score >= 65:
        target_pct = min(25.0, max(12.0, atr_pct * 5))
    elif total_score >= 45:
        target_pct = min(15.0, max(8.0, atr_pct * 3.5))
    else:
        target_pct = 8.0
    return price * (1 + target_pct / 100), price * (1 - stop_pct / 100)


# ── Main render ───────────────────────────────────────────────────────────────

def render_panel(symbol: str, row_data: dict) -> None:
    symbol = symbol.upper()
    price  = _safe(row_data.get("close"), 0)

    st.markdown(f"### 🔬 Phân Tích Trước Giao Dịch — **{symbol}**")
    if price > 0:
        signal = row_data.get("signal", "—")
        sector = row_data.get("sector", "—") or "—"
        st.caption(f"Giá: **{price:,.2f}** · Tín hiệu: **{signal}** · Ngành: {sector}")

    # ── Chọn phong cách đầu tư ───────────────────────────────────────────────
    style_key = st.radio(
        "Phong cách đầu tư",
        options=list(_STYLES.keys()),
        index=1,
        horizontal=True,
        key=f"pretrade_style_{symbol}",
    )
    weights = _STYLES[style_key]
    w_tech, w_fund, w_macro, w_ami = weights["tech"], weights["fund"], weights["macro"], weights["ami"]

    # ── Chạy 4 scoring + news ─────────────────────────────────────────────────
    with st.spinner("Đang lấy dữ liệu cơ bản & tin tức..."):
        fund   = _fetch_fundamental(symbol)
        regime = _fetch_market_regime()
        news_modifier, news_items, news_warnings = _score_news(symbol)

    tech_score,  tech_pros,  tech_cons  = _score_technical(row_data, w_tech)
    fund_score,  fund_pros,  fund_cons  = _score_fundamental(fund, w_fund)
    macro_score, macro_pros, macro_cons, macro_kpi, macro_ts = _score_macro(w_macro)
    ami_score,   ami_pros,   ami_cons   = _score_ami(row_data, w_ami)

    regime_label, regime_color, regime_modifier = _market_regime_label(regime)

    # Tổng = 4 sections + news modifier + market regime modifier
    base_score  = tech_score + fund_score + macro_score + ami_score
    total_score = max(0.0, min(100.0, base_score + news_modifier + regime_modifier))

    verdict_text, verdict_color = _verdict(total_score)
    target_price, stop_price   = _calc_targets(price, total_score, row_data)

    # ── Market regime warning ─────────────────────────────────────────────────
    if regime_modifier < 0:
        st.warning(f"⚠️ {regime_label} — điểm tổng bị điều chỉnh {regime_modifier:+d}")
    elif regime_modifier > 0:
        st.success(f"{regime_label} — điểm tổng bonus +{regime_modifier}")

    if macro_ts:
        st.caption(f"📅 Dữ liệu vĩ mô cập nhật: {macro_ts}" +
                   (" _(World Bank thường trễ 1-2 năm — chỉ dùng cho xu hướng)_" if macro_ts else ""))

    # ── News warnings nổi bật ─────────────────────────────────────────────────
    if news_warnings:
        st.error("🚨 **Tin trọng yếu tiêu cực:**\n" + "\n".join(f"- {w}" for w in news_warnings))

    # ── Verdict header ────────────────────────────────────────────────────────
    v_col1, v_col2, v_col3, v_col4, v_col5 = st.columns(5)
    v_col1.metric("Điểm Tổng",       f"{total_score:.0f}/100", delta=verdict_text, delta_color="off")
    v_col2.metric(f"KT ({w_tech}đ)",  f"{tech_score:.0f}")
    v_col3.metric(f"CB ({w_fund}đ)",  f"{fund_score:.0f}")
    v_col4.metric(f"VM ({w_macro}đ)", f"{macro_score:.0f}")
    v_col5.metric("Tin tức Δ",
                  f"{news_modifier:+.1f}",
                  delta="⚠️ tiêu cực" if news_modifier < -3 else ("✅ tích cực" if news_modifier > 3 else "trung tính"),
                  delta_color="inverse" if news_modifier < -3 else ("normal" if news_modifier > 3 else "off"))

    st.markdown(
        f'<div style="background:{verdict_color}22;border:2px solid {verdict_color};'
        f'border-radius:10px;padding:14px 20px;margin:8px 0">'
        f'<span style="font-size:1.4em;font-weight:bold;color:{verdict_color}">'
        f'{verdict_text}</span>'
        f'<span style="margin-left:16px;font-size:1em">Điểm: {total_score:.1f}/100</span>'
        f'<span style="margin-left:16px;font-size:0.85em;color:#ccc">{style_key}</span>'
        f'{"<br><small>⭐ BUY-A*: ngành top + RSI cao — alpha +2.08% kỳ vọng</small>" if row_data.get("signal")=="BUY-A*" else ""}'
        f'</div>',
        unsafe_allow_html=True,
    )

    if price > 0:
        t_col1, t_col2, t_col3 = st.columns(3)
        t_col1.metric("Giá vào",   f"{price:,.2f}")
        t_col2.metric("🎯 Target", f"{target_price:,.2f}",
                      delta=f"+{(target_price/price-1)*100:.1f}%", delta_color="normal")
        t_col3.metric("🛑 Stop",   f"{stop_price:,.2f}",
                      delta=f"-{(1-stop_price/price)*100:.1f}%", delta_color="inverse")

    st.divider()

    # ── 4 section chi tiết ────────────────────────────────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**📊 Kỹ thuật — {tech_score:.0f}/{w_tech}**")
        _mini_table_tech(row_data)
        for p in tech_pros: st.markdown(f"  🟢 {p}")
        for c in tech_cons: st.markdown(f"  🔴 {c}")
    with c2:
        st.markdown(f"**📈 Cơ bản — {fund_score:.0f}/{w_fund}**")
        _rp  = fund.get("report_period", "N/A") if fund else "N/A"
        _isq = fund.get("is_quarterly", False) if fund else False
        if _rp and _rp != "N/A":
            _lbl = f"quý **{_rp}**" if _isq else f"năm **{_rp}**"
            st.caption(f"📅 BCTC {_lbl}" + (" ✅ cập nhật ~3 tháng" if _isq else " ⚠️ fallback — quý không có"))
        _mini_table_fund(fund)
        for p in fund_pros: st.markdown(f"  🟢 {p}")
        for c in fund_cons: st.markdown(f"  🔴 {c}")

    c3, c4 = st.columns(2)
    with c3:
        st.markdown(f"**🌐 Vĩ mô — {macro_score:.0f}/{w_macro}**")
        if macro_kpi:
            for k, v in macro_kpi.items(): st.caption(f"**{k}:** {v}")
        for p in macro_pros: st.markdown(f"  🟢 {p}")
        for c in macro_cons: st.markdown(f"  🔴 {c}")
    with c4:
        st.markdown(f"**🤖 Amibroker — {ami_score:.0f}/{w_ami}**")
        _mini_table_ami(row_data)
        for p in ami_pros: st.markdown(f"  🟢 {p}")
        for c in ami_cons: st.markdown(f"  🔴 {c}")

    st.divider()

    # ── Section 5: Tin Tức ───────────────────────────────────────────────────
    st.markdown(f"**📰 Tin Tức — Modifier: {news_modifier:+.1f} điểm**")

    _SENT_ICON = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "🟡"}
    _SENT_COLOR = {"POSITIVE": "#1a3a2a", "NEGATIVE": "#3a1a1a", "NEUTRAL": "#2a2a1a"}

    if not news_items:
        st.caption("Không có tin tức trong 60 ngày gần đây.")
    else:
        # Phân nhóm
        material_items = [x for x in news_items if x.get("material_type")]
        regular_items  = [x for x in news_items if not x.get("material_type")]

        if material_items:
            st.markdown("**🔔 Tin trọng yếu:**")
            for item in sorted(material_items, key=lambda x: abs(x["weight"]), reverse=True)[:6]:
                icon  = _SENT_ICON[item["sentiment"]]
                mlbl  = _MATERIAL_LABEL.get(item.get("material_type",""), "📌")
                color = _SENT_COLOR[item["sentiment"]]
                st.markdown(
                    f'<div style="background:{color};border-radius:6px;padding:6px 10px;margin:3px 0;'
                    f'font-size:0.88em;color:#f0f0f0">'
                    f'{icon} {mlbl} <b>{item["date"]}</b> — {item["title"]}'
                    f'{"<br><small style=\'color:#cccccc\'>" + item["summary"] + "</small>" if item["summary"] else ""}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        with st.expander(f"📋 Tin thường ({len(regular_items)} bài)", expanded=False):
            for item in regular_items[:8]:
                icon = _SENT_ICON[item["sentiment"]]
                st.markdown(f"{icon} **{item['date']}** — {item['title']}")

    st.divider()

    # ── Tóm tắt lý do — tách theo 4 chiều ───────────────────────────────────
    _sections = [
        ("📊 Kỹ thuật",  tech_pros,  tech_cons),
        ("📈 Cơ bản",    fund_pros,  fund_cons),
        ("🌐 Vĩ mô",     macro_pros, macro_cons),
        ("🤖 Amibroker", ami_pros,   ami_cons),
    ]
    has_any = any(p or c for _, p, c in _sections)
    if has_any:
        with st.expander("📋 Tổng hợp lý do phân tích (theo từng chiều)"):
            for sec_name, sec_pros, sec_cons in _sections:
                if not sec_pros and not sec_cons:
                    continue
                st.markdown(f"**{sec_name}**")
                for p in sec_pros: st.markdown(f"  - ✅ {p}")
                for c in sec_cons: st.markdown(f"  - ⚠️ {c}")
                if sec_pros or sec_cons:
                    st.markdown("")  # spacing
            st.caption(
                "⚠️ Công thức scoring chưa được backtest đầy đủ. "
                "Sử dụng như tham khảo bổ sung, không phải khuyến nghị đầu tư."
            )

    # ── Lịch sử phân tích đã lưu ────────────────────────────────────────────
    render_saved_section(symbol)

    # ── Backtest results ──────────────────────────────────────────────────────
    try:
        render_backtest_section()
    except Exception as _bt_ex:
        st.caption(f"⚠️ Không thể hiển thị backtest results: {_bt_ex}")

    # ── Nút lưu phân tích ────────────────────────────────────────────────────
    _save_cols = st.columns([2, 4])
    with _save_cols[0]:
        _save_note = st.text_input(
            "Ghi chú (tùy chọn)", value="", placeholder="VD: chờ breakout, test support...",
            key=f"pretrade_note_{symbol}",
        )
    with _save_cols[1]:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 Lưu phân tích này", key=f"save_analysis_{symbol}", type="secondary"):
            _save_analysis(dict(
                symbol=symbol,
                saved_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                style=style_key,
                total_score=round(total_score, 1),
                tech_score=round(tech_score, 1),
                fund_score=round(fund_score, 1),
                macro_score=round(macro_score, 1),
                ami_score=round(ami_score, 1),
                news_modifier=round(news_modifier, 1),
                verdict=verdict_text,
                price=price,
                target_price=round(target_price, 2) if price > 0 else None,
                stop_price=round(stop_price, 2) if price > 0 else None,
                signal=row_data.get("signal", ""),
                sector=row_data.get("sector", ""),
                report_period=fund.get("report_period", "") if fund else "",
                pros=(tech_pros + fund_pros + macro_pros + ami_pros)[:10],
                cons=(tech_cons + fund_cons + macro_cons + ami_cons)[:10],
                note=_save_note.strip(),
            ))
            st.success(f"✅ Đã lưu phân tích {symbol} — {verdict_text} ({total_score:.0f}/100)")

    # ── Nút ghi Paper Trading ────────────────────────────────────────────────
    if total_score >= 45:
        from vn_invest.paper_trading import add_trade as _pt_add
        from vn_invest.screener import _SECTOR_MAP
        is_strong = total_score >= 65
        btn_lbl   = "📌 Ghi vào Paper Trading" if is_strong else "📌 Ghi vào Paper Trading (CHỜ)"
        if st.button(btn_lbl, key=f"pretrade_pt_{symbol}", type="primary" if is_strong else "secondary"):
            _pt_add(
                symbol=symbol,
                entry_price=price,
                tech_score=_safe(row_data.get("tech_score"), 0),
                rsi=_safe(row_data.get("rsi"), 0),
                signal=row_data.get("signal", "BUY-A"),
                sector=_SECTOR_MAP.get(symbol, row_data.get("sector", "")),
                note=f"Pre-trade score: {total_score:.0f}/100 | {style_key} | tin tức: {news_modifier:+.1f}",
            )
            st.success(f"✅ Đã ghi {symbol} vào Paper Trading — entry {price:,.2f}")


# ── Mini display helpers ──────────────────────────────────────────────────────

def _mini_table_tech(row: dict) -> None:
    rsi  = _safe(row.get("rsi"), 0)
    dist = _safe(row.get("dist_ema34_pct"), 0)
    volr = _safe(row.get("volume_ratio"), 0)
    atr  = _safe(row.get("atr_pct"), 0)
    wmt  = int(_safe(row.get("weekly_macd_trend"), 0))
    maal = int(_safe(row.get("ma_aligned"), 0))
    st.caption(
        f"RSI {rsi:.1f} · Dist {dist:+.1f}% · VolR {volr:.2f}× · ATR {atr:.1f}%  \n"
        f"Weekly MACD {'↑' if wmt>0 else '↓' if wmt<0 else '→'} · "
        f"MA Aligned {'✅' if maal>0 else '❌' if maal<0 else '–'}"
    )


def _mini_table_fund(fund: dict) -> None:
    if not fund:
        st.caption("—")
        return
    parts = []
    if fund.get("pe")  is not None: parts.append(f"P/E {fund['pe']:.1f}×")
    if fund.get("pb")  is not None: parts.append(f"P/B {fund['pb']:.1f}×")
    if fund.get("roe") is not None: parts.append(f"ROE {fund['roe']:.1f}%")  # KBS đã là %
    if fund.get("npm") is not None: parts.append(f"NPM {fund['npm']:.1f}%")  # KBS đã là %
    if fund.get("dte") is not None: parts.append(f"D/E {fund['dte']:.2f}×")
    st.caption("  ·  ".join(parts) if parts else "—")


# ── Lưu trữ phân tích ─────────────────────────────────────────────────────────

_SAVED_FILE = _APP_DIR / "data" / "saved_analyses.json"
_MAX_SAVED  = 200


def _load_saved_analyses() -> list[dict]:
    try:
        if _SAVED_FILE.exists():
            return json.loads(_SAVED_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_analysis(record: dict) -> None:
    records = _load_saved_analyses()
    records.insert(0, record)
    if len(records) > _MAX_SAVED:
        records = records[:_MAX_SAVED]
    _SAVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SAVED_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def render_saved_section(current_symbol: str) -> None:
    """Expander hiển thị lịch sử các phân tích đã lưu."""
    records = _load_saved_analyses()
    if not records:
        return

    sym_records = [r for r in records if r.get("symbol") == current_symbol]
    all_count   = len(records)

    with st.expander(
        f"📂 Lịch sử phân tích đã lưu — {current_symbol} ({len(sym_records)} bản) / tổng {all_count}",
        expanded=False,
    ):
        view_mode = st.radio(
            "Hiển thị",
            options=[f"Chỉ {current_symbol}", "Tất cả mã"],
            horizontal=True,
            key=f"saved_view_{current_symbol}",
        )
        show_records = sym_records if view_mode.startswith("Chỉ") else records

        if not show_records:
            st.caption("Chưa có bản lưu nào.")
            return

        _VERDICT_COLOR = {"MUA ✅": "#1a3a2a", "CHỜ 🟡": "#2a2a1a", "TRÁNH 🔴": "#3a1a1a"}

        for i, rec in enumerate(show_records[:30]):  # tối đa 30 bản trong view
            sym      = rec.get("symbol", "?")
            ts       = rec.get("saved_at", "")[:16]
            score    = rec.get("total_score", 0)
            verdict  = rec.get("verdict", "—")
            style    = rec.get("style", "")
            price    = rec.get("price", 0)
            signal   = rec.get("signal", "")
            bg_color = _VERDICT_COLOR.get(verdict, "#1a1a2a")
            note     = rec.get("note", "")

            st.markdown(
                f'<div style="background:{bg_color};color:#f0f0f0;border-radius:6px;'
                f'padding:8px 12px;margin:4px 0;font-size:0.87em">'
                f'<b>{sym}</b> &nbsp;·&nbsp; {ts} &nbsp;·&nbsp; '
                f'<b style="color:{"#4fc98a" if verdict.startswith("MUA") else "#f0b940" if verdict.startswith("CHỜ") else "#f05050"}">'
                f'{verdict}</b> &nbsp;·&nbsp; Điểm: <b>{score:.0f}/100</b> &nbsp;·&nbsp; '
                f'Giá: {price:,.0f} &nbsp;·&nbsp; Tín hiệu: {signal}'
                f'{"<br><small style=\'color:#aaaaaa\'>" + style + ("  |  " + note if note else "") + "</small>" if style else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )

        if len(show_records) > 30:
            st.caption(f"... và {len(show_records)-30} bản lưu khác (chỉ hiển thị 30 gần nhất).")

        st.divider()
        if st.button("🗑️ Xóa tất cả lịch sử phân tích", key=f"clear_saved_{current_symbol}", type="secondary"):
            _SAVED_FILE.write_text("[]", encoding="utf-8")
            st.success("Đã xóa toàn bộ lịch sử.")
            st.rerun()


def render_backtest_section() -> None:
    """
    Hiển thị kết quả backtest pre-trade scoring + nút chạy thủ công.
    Gọi từ render_panel() bên dưới bảng phân tích.
    """
    _RESULT_FILE   = _APP_DIR / "data" / "pretrade_backtest.json"
    _PROGRESS_FILE = _APP_DIR / "data" / "pretrade_backtest_progress.json"
    _BACKTEST_SCRIPT = _APP_DIR / "backtest_pretrade.py"

    with st.expander("📊 Backtest Kết Quả Pre-Trade Scoring (KT component)", expanded=False):

        # ── Kiểm tra trạng thái đang chạy ────────────────────────────────────
        progress: dict = {}
        if _PROGRESS_FILE.exists():
            try:
                progress = json.loads(_PROGRESS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        is_running = progress.get("status") in ("running", "starting", "aggregating")
        if is_running:
            pct  = progress.get("pct", 0)
            curr = progress.get("current", "")
            done = progress.get("done", 0)
            tot  = progress.get("total", 0)
            st.info(
                f"⏳ Backtest đang chạy nền... **{pct:.0f}%** ({done}/{tot} mã)"
                + (f" — đang xử lý: `{curr}`" if curr else "")
            )
            st.progress(min(pct / 100, 1.0))
            st.caption(f"Cập nhật lần cuối: {progress.get('updated_at', '')} — Làm mới trang để xem tiến độ mới.")
            return  # Không hiện kết quả cũ khi đang chạy

        # ── Đọc kết quả ───────────────────────────────────────────────────────
        if not _RESULT_FILE.exists():
            st.info("Chưa có dữ liệu backtest. Nhấn nút bên dưới để chạy lần đầu (~5-10 phút).")
        else:
            try:
                result = json.loads(_RESULT_FILE.read_text(encoding="utf-8"))
            except Exception:
                st.error("Lỗi đọc file backtest. Hãy chạy lại.")
                result = None

            if result:
                run_at = result.get("run_at", "")
                syms   = result.get("symbols_count", 0)
                pts    = result.get("data_points", 0)

                # Cảnh báo staleness
                try:
                    run_dt   = datetime.strptime(run_at, "%Y-%m-%d %H:%M")
                    age_days = (datetime.now() - run_dt).days
                    if age_days >= 30:
                        st.warning(
                            f"⚠️ Dữ liệu backtest đã **{age_days} ngày** — có thể lạc hậu. "
                            f"Nên chạy lại để cập nhật thị trường hiện tại."
                        )
                    elif age_days >= 14:
                        st.caption(f"ℹ️ Backtest chạy {age_days} ngày trước — cân nhắc cập nhật.")
                    else:
                        st.caption(f"✅ Backtest cập nhật: {run_at} · {syms} mã · {pts:,} data points")
                except Exception:
                    st.caption(f"Chạy lúc: {run_at}")

                # Verdict nổi bật
                verdict = result.get("verdict", "")
                if verdict:
                    st.markdown(
                        f'<div style="background:#1a2a3a;color:#90caf9;border-radius:6px;'
                        f'padding:8px 12px;margin:4px 0;font-size:0.9em">'
                        f'🎯 <b>Kết luận:</b> {verdict}</div>',
                        unsafe_allow_html=True,
                    )

                note = result.get("note", "")
                if note:
                    st.caption(f"📝 {note}")

                st.divider()

                # Hàm tô màu dùng chung cho cả 2 bảng — define 1 lần, dùng cả by_signal lẫn by_score
                import pandas as pd

                def _color_avg(val):
                    if val is None or (isinstance(val, float) and math.isnan(val)):
                        return ""
                    try:
                        v = float(val)
                        if v > 1.5:   return "color:#4caf50;font-weight:bold"
                        if v > 0:     return "color:#81c784"
                        if v > -1.5:  return "color:#ef9a9a"
                        return "color:#f44336;font-weight:bold"
                    except Exception:
                        return ""

                # ── Bảng theo signal ──────────────────────────────────────────
                by_signal = result.get("by_signal", {})
                if by_signal:
                    st.markdown("**Theo tín hiệu KT (T+5 / T+10 / T+20 avg return)**")
                    _SIGNAL_ORDER = ["BUY-A*", "BUY-A", "BUY-B", "HOLD", "SELL-B", "SELL-A"]
                    rows_tbl = []
                    for sig in _SIGNAL_ORDER:
                        if sig not in by_signal:
                            continue
                        d = by_signal[sig]
                        rows_tbl.append({
                            "Tín hiệu":   sig,
                            "Số lần":     d.get("n_total", 0),
                            "Avg T+5 %":  d["t5"]["avg"]  if d.get("t5")  else None,
                            "Avg T+10 %": d["t10"]["avg"] if d.get("t10") else None,
                            "Avg T+20 %": d["t20"]["avg"] if d.get("t20") else None,
                            "Win T+10":   f"{d['t10']['win_rate']:.0%}" if d.get("t10") and d["t10"].get("win_rate") is not None else "—",
                        })
                    if rows_tbl:
                        df_sig = pd.DataFrame(rows_tbl)
                        st.dataframe(
                            df_sig.style
                            .applymap(_color_avg, subset=["Avg T+5 %", "Avg T+10 %", "Avg T+20 %"])
                            .format({"Avg T+5 %": "{:+.2f}", "Avg T+10 %": "{:+.2f}", "Avg T+20 %": "{:+.2f}"},
                                    na_rep="—"),
                            use_container_width=True,
                            hide_index=True,
                        )

                # ── Bảng theo score bucket ────────────────────────────────────
                by_score = result.get("by_score", {})
                if by_score:
                    st.markdown("**Theo score bucket (tech_score)**")
                    rows_bkt = []
                    for label, d in by_score.items():
                        rows_bkt.append({
                            "Nhóm":       label,
                            "Số lần":     d.get("n_total", 0),
                            "Avg T+5 %":  d["t5"]["avg"]  if d.get("t5")  else None,
                            "Avg T+10 %": d["t10"]["avg"] if d.get("t10") else None,
                            "Avg T+20 %": d["t20"]["avg"] if d.get("t20") else None,
                            "Win T+10":   f"{d['t10']['win_rate']:.0%}" if d.get("t10") and d["t10"].get("win_rate") is not None else "—",
                        })
                    if rows_bkt:
                        df_bkt = pd.DataFrame(rows_bkt)
                        st.dataframe(
                            df_bkt.style
                            .applymap(_color_avg, subset=["Avg T+5 %", "Avg T+10 %", "Avg T+20 %"])
                            .format({"Avg T+5 %": "{:+.2f}", "Avg T+10 %": "{:+.2f}", "Avg T+20 %": "{:+.2f}"},
                                    na_rep="—"),
                            use_container_width=True,
                            hide_index=True,
                        )

        # ── Nút chạy thủ công ─────────────────────────────────────────────────
        st.divider()
        btn_col, info_col = st.columns([2, 3])
        with btn_col:
            run_clicked = st.button(
                "🔄 Chạy Backtest",
                key="pretrade_run_backtest",
                type="primary",
                help="Chạy nền ~5-10 phút. Làm mới trang để xem tiến độ.",
            )
        with info_col:
            if _RESULT_FILE.exists():
                try:
                    _rt = json.loads(_RESULT_FILE.read_text(encoding="utf-8")).get("run_at", "")
                    st.caption(f"Lần chạy cuối: {_rt}")
                except Exception:
                    pass
            st.caption("Backtest chạy ngầm. **Không đóng app** trong lúc chạy.")

        if run_clicked:
            if not _BACKTEST_SCRIPT.exists():
                st.error(f"Không tìm thấy script: {_BACKTEST_SCRIPT}")
            else:
                try:
                    subprocess.Popen(
                        [sys.executable, str(_BACKTEST_SCRIPT)],
                        cwd=str(_APP_DIR),
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    # Ghi trạng thái khởi động ngay để UI biết
                    _PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
                    _PROGRESS_FILE.write_text(
                        json.dumps({"status": "starting", "done": 0, "total": 0,
                                    "current": "", "pct": 0,
                                    "updated_at": datetime.now().strftime("%H:%M:%S")},
                                   ensure_ascii=False),
                        encoding="utf-8",
                    )
                    st.success("✅ Backtest đã khởi động nền. Làm mới trang sau vài phút để xem tiến độ.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Không thể khởi động backtest: {e}")


def _mini_table_ami(row: dict) -> None:
    rec  = row.get("ami_rec_label", "—") or "—"
    sc   = _safe(row.get("ami_score"), 0)
    comp = _safe(row.get("composite_score"), 0)
    setup = row.get("ami_setup", "—") or "—"
    fore  = row.get("ami_forecast", "—") or "—"
    st.caption(
        f"Rec: {rec} ({sc:.0f}) · Composite: {comp:.0f}  \n"
        f"Setup: {setup} · Forecast: {fore}"
    )
