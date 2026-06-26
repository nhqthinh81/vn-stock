"""Dashboard phân tích chứng khoán Việt Nam — Streamlit 5 tabs."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
_api_key = os.getenv("VNSTOCK_API_KEY")
if _api_key:
    try:
        from vnstock import change_api_key
        change_api_key(_api_key)
    except Exception:
        pass

import pandas as pd
import streamlit as st
import threading

from vn_invest.data import (get_price_history, get_financial_ratios_history, get_company_overview,
                            get_company_news, get_company_events, get_company_dividends,
                            get_company_shareholders, get_financial_statements, get_stock_status,
                            get_side_stats, get_market_indices, get_capital_history, get_macro_data,
                            get_market_breadth)
from vn_invest.investing import COMMON_PAIRS, get_global_price
from vn_invest.indicators import add_all_indicators, get_latest_signals
from vn_invest.lstm import predict as lstm_predict, model_ready, get_model_info
from vn_invest.screener import (load_cache, load_cache_meta, scan_ami_watchlist, scan_ami_symbol,
                                get_ami_watchlist, get_all_ami_symbols, get_ami_scan_age,
                                refresh_prices, refresh_signals_from_ami, filter_cache, scan_symbol,
                                _BAD_STATUSES)
from vn_invest.config import RESTRICTED_SYMBOLS
from vn_invest.portfolio import (load_portfolio, load_portfolio_manual, save_portfolio_manual,
                                  enrich_portfolio, portfolio_summary, sector_allocation,
                                  fetch_sector_batch)

# ── Cache functions (module-level để Streamlit hash đúng) ────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_overview(sym, src):
    # VCI trả organ_name/organ_short_name/sector, KBS trả exchange/ceo_name/address
    vci = get_company_overview(sym, source="VCI")
    kbs = get_company_overview(sym, source=src) if src != "VCI" else {}
    merged = {**kbs, **{k: v for k, v in vci.items() if v}}
    # Chuẩn hóa field tên/ngành
    merged.setdefault("company_name", merged.get("organ_name", ""))
    merged.setdefault("short_name",   merged.get("organ_short_name", ""))
    merged.setdefault("industry_name",merged.get("sector", ""))
    return merged

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_ratio_hist(sym, period, n, src):
    return get_financial_ratios_history(sym, period=period, source=src, n_periods=n)

@st.cache_data(ttl=900, show_spinner=False)
def _fetch_stock_status(sym):
    return get_stock_status(sym, source="VCI")

@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_statements(sym, period):
    return get_financial_statements(sym, period=period, source="VCI")

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_shareholders(sym):
    return get_company_shareholders(sym, source="VCI")

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_news(sym):
    return get_company_news(sym, source="VCI")

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_events(sym):
    return get_company_events(sym, source="VCI")

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_dividends(sym):
    return get_company_dividends(sym, source="VCI")

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_price(sym, d, src):
    from vn_invest.indicators import add_all_indicators
    try:
        if sym in COMMON_PAIRS or "/" in sym:
            from datetime import datetime, timedelta
            start = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d")
            end = datetime.now().strftime("%Y-%m-%d")
            df = get_global_price(sym, start=start, end=end)
        else:
            df = get_price_history(sym, days=d, source=src)
    except Exception:
        return None
    return add_all_indicators(df) if df is not None and not df.empty else None

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_macro_ticker_data():
    results = {}
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    for macro_sym in ["SP500", "NASDAQ", "DOW", "GOLD", "BRENT", "WTI", "BTC/USD", "EUR/USD", "USD/VND", "DXY"]:
        try:
            df = get_global_price(macro_sym, start=start, end=end)
            if df is not None and not df.empty:
                current_price = df.iloc[-1]['close']
                if len(df) > 1:
                    prev_price = df.iloc[-2]['close']
                    pct_change = (current_price - prev_price) / prev_price * 100
                    results[macro_sym] = (current_price, pct_change)
                else:
                    results[macro_sym] = (current_price, None)
        except Exception:
            pass
    return results

# Tickers yfinance cho các chỉ số Châu Á
_ASIA_INDICES = {
    "Nikkei":   "^N225",   # Nhật Bản
    "Hang Seng":"^HSI",    # Hồng Kông
    "Shanghai": "000001.SS",# Trung Quốc
    "KOSPI":    "^KS11",   # Hàn Quốc
    "ASX200":   "^AXJO",   # Úc
    "STI":      "^STI",    # Singapore
}

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_asia_indices():
    try:
        import yfinance as yf
        results = {}
        tickers = yf.Tickers(" ".join(_ASIA_INDICES.values()))
        for label, yfticker in _ASIA_INDICES.items():
            try:
                hist = tickers.tickers[yfticker].history(period="5d")
                if hist is not None and len(hist) >= 1:
                    cur = float(hist["Close"].iloc[-1])
                    pct = float((hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2] * 100) if len(hist) >= 2 else None
                    results[label] = (cur, pct)
            except Exception:
                pass
        return results
    except Exception:
        return {}

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_side_stats(sym, src):
    return get_side_stats(sym, source=src)

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_market_indices():
    return get_market_indices()

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_all_listed_symbols() -> tuple:
    """Lấy toàn bộ mã niêm yết HOSE+HNX+UPCOM — cache 1 giờ."""
    try:
        from vnstock import Listing
        lst = Listing()
        all_syms = []
        for ex in ("HOSE", "HNX", "UPCOM"):
            try:
                df = lst.symbols_by_exchange(exchange=ex)
                if df is not None and not df.empty:
                    col = next((c for c in df.columns if "symbol" in c.lower() or "ticker" in c.lower()), None)
                    if col:
                        all_syms.extend(df[col].dropna().astype(str).tolist())
            except Exception:
                pass
        return tuple(dict.fromkeys(all_syms))  # dedupe, preserve order
    except Exception:
        return ()

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_market_breadth(symbols_key: str, symbols: tuple):
    return get_market_breadth(list(symbols))

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_capital_history(sym):
    return get_capital_history(sym, source="VCI")

@st.cache_data(ttl=21600, show_spinner=False)  # 6 tiếng — IMF WEO cập nhật theo quý
def _fetch_macro():
    return get_macro_data()

# ── Paths ─────────────────────────────────────────────────────────────────────
_APP_DIR     = Path(__file__).parent
_METRICS_FILE = _APP_DIR / "data" / "model_metrics.json"
_TRAIN_LOG    = _APP_DIR / "data" / "train_running.log"
_V7_MODEL     = Path(r"C:\AmibrokerData\stock_lstm_v7.keras")
_V6_MODEL     = Path(r"C:\AmibrokerData\stock_lstm_v6_multi.keras")

# ── Cấu hình trang ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="VN Invest Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .metric-card { background:#1e2130;border-radius:10px;padding:16px 20px;margin:4px 0 }
    .signal-buy-a  { color:#00e676;font-weight:bold }
    .signal-buy-b  { color:#69f0ae;font-weight:bold }
    .signal-hold   { color:#ffd740;font-weight:bold }
    .signal-sell-b { color:#ff6d00;font-weight:bold }
    .signal-sell-a { color:#ff1744;font-weight:bold }
    .risk-low    { color:#00e676 }
    .risk-medium { color:#ffd740 }
    .risk-high   { color:#ff1744 }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 VN Invest")
    st.caption("Phân tích chứng khoán Việt Nam")
    st.divider()

    # ── Chỉ số thị trường ──────────────────────────────────────────────────
    _indices = _fetch_market_indices()
    _IDX_LABEL = {
        "VNINDEX": "VN-Index", "HNXINDEX": "HNX",
        "UPCOMINDEX": "UPCOM",  "VN30": "VN30", "HNX30": "HNX30",
    }
    _IDX_ORDER = ["VNINDEX", "HNXINDEX", "UPCOMINDEX", "VN30"]
    _idx_map = {r["index_id"]: r for r in _indices}
    _show = [_idx_map[k] for k in _IDX_ORDER if k in _idx_map]
    if _show:
        _cols = st.columns(len(_show))
        for _ci, _idx in enumerate(_show):
            _val = _idx.get("index_value")
            _pct = _idx.get("pct_change")
            _chg = _idx.get("change")
            _lbl = _IDX_LABEL.get(_idx["index_id"], _idx["index_id"])
            _val_str = f"{_val:,.2f}" if _val else "—"
            _delta_str = f"{_pct:+.2f}%" if _pct is not None else (f"{_chg:+.2f}" if _chg else None)
            _cols[_ci].metric(_lbl, _val_str, _delta_str, delta_color="normal")
        st.divider()

    # ── Độ rộng thị trường ────────────────────────────────────────────────
    # Dùng danh sách toàn thị trường (HOSE+HNX+UPCOM), không phụ thuộc watchlist
    _all_listed = _fetch_all_listed_symbols()
    if not _all_listed:
        _cache_syms_fb = st.session_state.get("scan_cache") or load_cache()
        _all_listed = tuple(r["symbol"] for r in _cache_syms_fb if r.get("symbol")) if _cache_syms_fb else ()
    _breadth: dict = {}
    if _all_listed:
        _breadth = _fetch_market_breadth(f"breadth_{len(_all_listed)}", _all_listed)
    # scan_cache chỉ dùng cho signal distribution bên dưới
    _cache_syms = st.session_state.get("scan_cache") or load_cache()
    if _breadth:

        def _breadth_row(ex_label: str, ex_key: str):
            b = _breadth.get(ex_key, {})
            if not b or b.get("total", 0) == 0:
                return
            adv = b.get("advance", 0)
            dec = b.get("decline", 0)
            unc = b.get("unchanged", 0)
            cei = b.get("ceiling", 0)
            flo = b.get("floor", 0)
            tot = b.get("total", 1)
            adv_pct = adv / tot * 100
            dec_pct = dec / tot * 100
            # Progress bar: green=advance, red=decline
            _bar_html = (
                f'<div style="display:flex;height:6px;border-radius:3px;overflow:hidden;margin:2px 0 4px">'
                f'<div style="width:{adv_pct:.1f}%;background:#00c853"></div>'
                f'<div style="width:{dec_pct:.1f}%;background:#ff1744"></div>'
                f'<div style="flex:1;background:#333"></div>'
                f'</div>'
            )
            cei_str = f' <span style="color:#ff9800;font-size:10px">⬆{cei}trần</span>' if cei else ""
            flo_str = f' <span style="color:#9c27b0;font-size:10px">⬇{flo}sàn</span>' if flo else ""
            st.markdown(
                f'<div style="font-size:11px;font-weight:600;color:#aaa;margin-bottom:1px">{ex_label}</div>'
                f'{_bar_html}'
                f'<div style="font-size:11px;display:flex;gap:8px">'
                f'<span style="color:#00c853">▲{adv}</span>'
                f'<span style="color:#ff1744">▼{dec}</span>'
                f'<span style="color:#888">→{unc}</span>'
                f'{cei_str}{flo_str}'
                f'</div>',
                unsafe_allow_html=True
            )

        if _breadth:
            st.markdown('<div style="font-size:12px;font-weight:700;color:#ddd;margin-bottom:6px">Độ rộng thị trường</div>',
                        unsafe_allow_html=True)
            _breadth_row("HOSE", "HOSE")
            _breadth_row("HNX", "HNX")
            _breadth_row("UPCOM", "UPCOM")

            # Signal distribution từ cache (watchlist)
            _sig_counts: dict[str, int] = {}
            for _r in (_cache_syms or []):
                _s = _r.get("signal") or _r.get("signal_class") or "HOLD"
                _sig_counts[_s] = _sig_counts.get(_s, 0) + 1
            _tot_sig = sum(_sig_counts.values()) or 1
            _SIG_COLOR = {"BUY-A": "#00c853", "BUY-B": "#69f0ae", "HOLD": "#888",
                          "SELL-B": "#ff8a65", "SELL-A": "#ff1744"}
            _sig_bar = "".join(
                f'<div style="width:{_sig_counts.get(s,0)/_tot_sig*100:.1f}%;background:{c};height:8px" title="{s}:{_sig_counts.get(s,0)}"></div>'
                for s, c in _SIG_COLOR.items()
            )
            _sig_labels = " ".join(
                f'<span style="color:{c};font-size:10px">{s}:{_sig_counts.get(s,0)}</span>'
                for s, c in _SIG_COLOR.items() if _sig_counts.get(s, 0) > 0
            )
            st.markdown(
                f'<div style="font-size:11px;font-weight:600;color:#aaa;margin-top:6px;margin-bottom:2px">Tín hiệu ({len(_cache_syms)} mã)</div>'
                f'<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin-bottom:3px">{_sig_bar}</div>'
                f'<div style="display:flex;flex-wrap:wrap;gap:4px">{_sig_labels}</div>',
                unsafe_allow_html=True
            )
            st.divider()

    # ── Chỉ số Vĩ mô & Châu Á — Scrolling Ticker ─────────────────────────
    macro_data  = _fetch_macro_ticker_data()
    asia_data   = _fetch_asia_indices()

    # VNINDEX từ _indices đã fetch sẵn (không cần gọi thêm API)
    _vnidx = _idx_map.get("VNINDEX")
    vnindex_entry = {}
    if _vnidx:
        _v = _vnidx.get("index_value")
        _p = _vnidx.get("pct_change")
        if _v is not None:
            vnindex_entry = {"VN-Index": (_v, _p)}

    # Ghép: VN-Index → Châu Á → Toàn cầu
    combined = {**vnindex_entry, **asia_data, **macro_data}

    if combined:
        def _ticker_item(sym, price, pct):
            val_str = f"{price:,.2f}" if price is not None else "—"
            if pct is not None:
                color = "#00c853" if pct >= 0 else "#ff1744"
                arrow = "▲" if pct >= 0 else "▼"
                pct_str = f'<span style="color:{color}">{arrow}{abs(pct):.2f}%</span>'
            else:
                pct_str = '<span style="color:#888">—</span>'
            return (
                f'<span style="color:#aaa;font-weight:600">{sym}</span>'
                f'&nbsp;<span style="color:#fff">{val_str}</span>'
                f'&nbsp;{pct_str}'
                f'&nbsp;&nbsp;<span style="color:#333">|</span>&nbsp;&nbsp;'
            )

        items_html = "".join(
            _ticker_item(sym, price, pct)
            for sym, (price, pct) in combined.items()
        )
        ticker_content = items_html * 2
        speed = max(30, len(combined) * 3)

        st.markdown(
            f"""
<style>
@keyframes vni-scroll {{
  0%   {{ transform: translateX(0); }}
  100% {{ transform: translateX(-50%); }}
}}
.vni-ticker-wrap {{
  overflow: hidden;
  background: #0e1117;
  border: 1px solid #2d3139;
  border-radius: 6px;
  padding: 5px 0;
  margin-bottom: 4px;
}}
.vni-ticker-inner {{
  display: inline-block;
  white-space: nowrap;
  animation: vni-scroll {speed}s linear infinite;
  font-size: 12px;
  font-family: monospace;
}}
.vni-ticker-inner:hover {{ animation-play-state: paused; }}
</style>
<div class="vni-ticker-wrap">
  <div class="vni-ticker-inner">{ticker_content}</div>
</div>
""",
            unsafe_allow_html=True,
        )
        st.divider()

    from vn_invest.watchlist import load_watchlist, add_to_watchlist, remove_from_watchlist
    _wl_symbols = load_watchlist()

    symbol_input = st.text_input("Mã cổ phiếu", value="HPG", max_chars=15).upper().strip()
    source = st.selectbox("Nguồn dữ liệu", ["KBS", "VCI"], index=0)
    days = st.slider("Lịch sử (ngày)", 60, 365, 120)

    # ── Watchlist ──────────────────────────────────────────────────────────────
    st.divider()
    with st.expander(f"⭐ Watchlist ({len(_wl_symbols)} mã)", expanded=False):
        if _wl_symbols:
            for _ws in _wl_symbols:
                _wc1, _wc2 = st.columns([3, 1])
                _wc1.write(_ws)
                if _wc2.button("✕", key=f"rm_{_ws}", use_container_width=True):
                    remove_from_watchlist(_ws)
                    st.rerun()
        else:
            st.caption("Chưa có mã nào. Thêm bằng nút ⭐ bên Tab Kỹ Thuật.")

        _add_wl_col1, _add_wl_col2 = st.columns([3, 1])
        _new_sym = _add_wl_col1.text_input("Thêm mã", max_chars=10,
                                            placeholder="VNM", label_visibility="collapsed")
        if _add_wl_col2.button("Thêm", use_container_width=True) and _new_sym:
            if add_to_watchlist(_new_sym.upper()):
                st.success(f"Đã thêm {_new_sym.upper()}")
                st.rerun()
            else:
                st.warning("Mã đã có trong watchlist.")

    st.divider()
    st.caption("v1.0.0 | vnstock + Streamlit")
    st.markdown(
        '<p style="font-size:10px;color:#666;margin-top:4px">'
        '⚠️ Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.'
        '</p>',
        unsafe_allow_html=True,
    )

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab_basic, tab_tech, tab_scan, tab_port, tab_model, tab_phaisinh, tab_news = st.tabs([
    "📊 Cơ Bản", "📉 Kỹ Thuật", "🔍 Quick Scan", "💼 Danh Mục", "🤖 Model AI", "⚡ Phái Sinh", "📰 Tin Tức"
])

# ═════════════════════════════════════════════════════════════════════════════
# CHATBOT HELPERS  (phải định nghĩa trước các tab vì được gọi trong tab_basic)
# ═════════════════════════════════════════════════════════════════════════════

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


_CHAT_HISTORY_FILE = Path(__file__).parent / "data" / "chat_history.json"

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


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — CƠ BẢN
# ═════════════════════════════════════════════════════════════════════════════
with tab_basic:
    import plotly.graph_objects as go

    # ── Độ rộng thị trường (3 sàn) ───────────────────────────────────────────
    # Dùng session_state để tránh gọi price_board mỗi lần rerun (gây treo khi đổi filter)
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
            # Màu tiêu đề theo xu hướng
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
        # Dòng 1: Tên đầy đủ
        full_name = overview.get("company_name") or overview.get("organ_name") or "—"
        short_name = overview.get("short_name") or overview.get("organ_short_name") or symbol_input
        st.markdown(f"### {short_name} &nbsp; <small style='color:#888;font-weight:normal'>{full_name}</small>",
                    unsafe_allow_html=True)

        # Dòng 2: Thông tin cơ bản
        ov_c1, ov_c2, ov_c3, ov_c4, ov_c5 = st.columns(5)
        ov_c1.metric("Sàn", overview.get("exchange", "—"))
        ov_c2.metric("Ngành", overview.get("industry_name") or overview.get("sector") or "—")
        ov_c3.metric("Loại hình", overview.get("company_type", "—"))

        emp = overview.get("number_of_employees")
        ov_c4.metric("Nhân viên", f"{int(emp):,}" if emp else "—")

        cap = overview.get("charter_capital")
        ov_c5.metric("Vốn điều lệ (tỷ)", f"{float(cap):,.0f}" if cap else "—")

        # Dòng 3: CEO, website, ngày niêm yết
        meta_parts = []
        if overview.get("ceo_name"):      meta_parts.append(f"**CEO:** {overview['ceo_name']}")
        if overview.get("listing_date"):  meta_parts.append(f"**Niêm yết:** {str(overview['listing_date'])[:10]}")
        if overview.get("auditor"):       meta_parts.append(f"**Kiểm toán:** {overview['auditor']}")
        if overview.get("website"):       meta_parts.append(f"[🌐 Website]({overview['website']})")
        if meta_parts:
            st.markdown(" &nbsp;·&nbsp; ".join(meta_parts), unsafe_allow_html=True)

        # Mô tả công ty (nếu có)
        profile = overview.get("company_profile") or overview.get("business_model") or ""
        if profile and len(str(profile).strip()) > 20:
            with st.expander("📋 Giới thiệu công ty", expanded=False):
                st.markdown(str(profile)[:2000])

        # ── Nút tạo + xem báo cáo equity research ───────────────────────────
        st.markdown("---")
        _REPORT_DIR = os.path.join(os.path.expanduser("~"), "equity_reports")

        def _find_report(sym: str) -> tuple[str | None, str | None]:
            """Tìm file HTML báo cáo cho mã sym. Trả (path, ngày_tạo) hoặc (None, None)."""
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

        # ── Hiển thị báo cáo HTML inline ────────────────────────────────────
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

        # ── Hướng dẫn tạo báo cáo mới ───────────────────────────────────────
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

    # Banner nổi bật nếu có vấn đề
    if ss_status == "delisted":
        st.error("🚫 **CẢNH BÁO: Cổ phiếu đã bị hủy niêm yết hoặc đang giao dịch ngoài sàn (OTC)**")
    elif ss_status == "suspended":
        st.error("⏸ **CẢNH BÁO: Cổ phiếu đang bị tạm ngừng giao dịch**")
    elif ss_status in ("restricted", "warning"):
        st.warning("⚠️ **CHÚ Ý: Cổ phiếu trong diện cảnh báo hoặc bị hạn chế giao dịch**")

    # Badges trạng thái + KPI giao dịch cùng 1 hàng
    badge_html = " &nbsp; ".join(
        f'<span style="background:{"#cc3333" if b["level"]=="danger" else "#aa7700" if b["level"]=="warning" else "#1a6b3c"};'
        f'color:#fff;padding:3px 10px;border-radius:12px;font-size:0.85em">{b["label"]}</span>'
        for b in ss_badges
    )
    st.markdown(badge_html, unsafe_allow_html=True)

    # KPI giao dịch từ trading_stats
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

    # Sự kiện cảnh báo từ events
    if ss_events:
        with st.expander(f"📋 Sự kiện cảnh báo/hạn chế ({len(ss_events)})", expanded=True):
            for ev in ss_events:
                level = "🚫" if "hủy" in ev["title"].lower() or ev["code"] == "SUSP" else "⚠️"
                st.markdown(f"{level} **{ev['date']}** — {ev['title']} `{ev['code']}`")

    # Chi tiết cảnh báo
    for alert in ss_alerts:
        st.caption(f"ℹ️ {alert}")

    st.divider()

    periods       = hist["periods"]
    hdata         = hist["data"]
    actual_period = hist.get("actual_period", api_period)
    actual_source = hist.get("actual_source", source)

    # Thông báo giới hạn dữ liệu vnstock (~4 kỳ gần nhất)
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

    # Giá trị mới nhất (cột đầu tiên = kỳ gần nhất)
    latest = {k: (v[0] if v else None) for k, v in hdata.items()}

    if not periods:
        st.info("Không có dữ liệu tài chính. Kiểm tra mã hoặc nguồn dữ liệu.")
    else:
        # ── KPI cards kỳ gần nhất ────────────────────────────────────────────
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

        # ── Biểu đồ xu hướng theo nhóm ───────────────────────────────────────
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

        # Đảo periods để biểu đồ chạy từ cũ → mới (trái → phải)
        x_labels = list(reversed(periods))

        def _trend_chart(keys_in_group):
            fig = go.Figure()
            has_data = False
            for key in keys_in_group:
                if key not in hdata:
                    continue
                vals_raw = hdata[key]
                # Đảo lại (cũ → mới)
                y_vals = list(reversed(vals_raw))
                # None → vẫn vẽ nhưng bỏ trống
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

        # ── Bảng số liệu đầy đủ ──────────────────────────────────────────────
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

        # ── Nhận định tự động ────────────────────────────────────────────────
        st.subheader("Nhận định tự động")

        def _v(label, cond_good, cond_warn, msg_good, msg_warn, msg_bad):
            if cond_good:   st.success(f"**{label}**: {msg_good}")
            elif cond_warn: st.warning(f"**{label}**: {msg_warn}")
            else:           st.error(f"**{label}**: {msg_bad}")

        def _trend(key, periods_list, data):
            """Tính xu hướng: +/- dựa vào 2 kỳ gần nhất."""
            vals = data.get(key, [])
            clean = [v for v in vals if v is not None]
            if len(clean) < 2:
                return ""
            chg = clean[0] - clean[1]
            pct = chg / abs(clean[1]) * 100 if clean[1] != 0 else 0
            arrow = "↑" if chg > 0 else "↓"
            return f" ({arrow}{abs(pct):.1f}% so kỳ trước)"

        # ── Nhóm 1: Định giá ─────────────────────────────────────────────────
        pe = latest.get("p_e")
        pb = latest.get("p_b")
        eps = latest.get("trailing_eps")
        _nd_col1, _nd_col2 = st.columns(2)
        with _nd_col1:
            if pe is not None:
                _t = _trend("p_e", periods, hdata)
                _v("Định giá (P/E)", pe < 15, pe < 25,
                   f"P/E = {pe:.1f}{_t} — khá rẻ",
                   f"P/E = {pe:.1f}{_t} — trung bình",
                   f"P/E = {pe:.1f}{_t} — cao, cần thận trọng")
            elif pb is not None:
                _v("Định giá (P/B)", pb < 1.0, pb < 2.5,
                   f"P/B = {pb:.2f} — dưới giá trị sổ sách",
                   f"P/B = {pb:.2f} — hợp lý",
                   f"P/B = {pb:.2f} — cao")
            else:
                st.info("**Định giá**: Không có dữ liệu P/E, P/B từ nguồn hiện tại.")

            if pb is not None and pe is not None:
                _v("Định giá (P/B)", pb < 1.0, pb < 2.5,
                   f"P/B = {pb:.2f} — dưới giá trị sổ sách",
                   f"P/B = {pb:.2f} — hợp lý",
                   f"P/B = {pb:.2f} — cao")

            if eps is not None:
                _t = _trend("trailing_eps", periods, hdata)
                _v("EPS (trailing)", eps > 0, eps > -1000,
                   f"EPS = {eps:,.0f} VNĐ{_t} — có lãi",
                   f"EPS = {eps:,.0f} VNĐ{_t} — biên mỏng",
                   f"EPS = {eps:,.0f} VNĐ{_t} — lỗ")

        # ── Nhóm 2: Sinh lời ─────────────────────────────────────────────────
        with _nd_col2:
            roe = latest.get("roe")
            roa = latest.get("roa")
            gpm = latest.get("gross_profit_margin")
            npm = latest.get("net_profit_margin")

            if roe is not None:
                _t = _trend("roe", periods, hdata)
                _v("Sinh lời (ROE)", roe > 15, roe > 8,
                   f"ROE = {roe:.1f}%{_t} — tốt (>15%)",
                   f"ROE = {roe:.1f}%{_t} — trung bình (8-15%)",
                   f"ROE = {roe:.1f}%{_t} — thấp (<8%)")
            else:
                st.info("**Sinh lời (ROE)**: Không có dữ liệu.")

            if roa is not None:
                _t = _trend("roa", periods, hdata)
                _v("Sinh lời (ROA)", roa > 8, roa > 3,
                   f"ROA = {roa:.1f}%{_t} — tốt",
                   f"ROA = {roa:.1f}%{_t} — trung bình",
                   f"ROA = {roa:.1f}%{_t} — thấp")

            if gpm is not None:
                _t = _trend("gross_profit_margin", periods, hdata)
                _v("Biên lãi gộp", gpm > 30, gpm > 15,
                   f"Biên gộp = {gpm:.1f}%{_t} — cao, lợi thế cạnh tranh tốt",
                   f"Biên gộp = {gpm:.1f}%{_t} — trung bình",
                   f"Biên gộp = {gpm:.1f}%{_t} — thấp, áp lực chi phí")

            if npm is not None:
                _t = _trend("net_profit_margin", periods, hdata)
                _v("Biên lãi ròng", npm > 15, npm > 5,
                   f"Biên ròng = {npm:.1f}%{_t} — xuất sắc",
                   f"Biên ròng = {npm:.1f}%{_t} — ổn",
                   f"Biên ròng = {npm:.1f}%{_t} — thấp")

        # ── Nhóm 3+4: Đòn bẩy & Thanh khoản ─────────────────────────────────
        _nd_col3, _nd_col4 = st.columns(2)
        with _nd_col3:
            de  = latest.get("debt_to_equity")
            da  = latest.get("debt_to_assets")
            ic  = latest.get("interest_coverage")

            if de is not None:
                _t = _trend("debt_to_equity", periods, hdata)
                _v("Đòn bẩy (Nợ/VCSH)", de < 1.0, de < 2.0,
                   f"Nợ/VCSH = {de:.2f}{_t} — lành mạnh",
                   f"Nợ/VCSH = {de:.2f}{_t} — chấp nhận được",
                   f"Nợ/VCSH = {de:.2f}{_t} — cao, rủi ro tài chính")
            elif da is not None:
                _t = _trend("debt_to_assets", periods, hdata)
                _v("Đòn bẩy (Nợ/TS)", da < 0.4, da < 0.6,
                   f"Nợ/TS = {da:.1f}%{_t} — thấp, lành mạnh",
                   f"Nợ/TS = {da:.1f}%{_t} — trung bình",
                   f"Nợ/TS = {da:.1f}%{_t} — cao")
            else:
                st.info("**Đòn bẩy**: Không có dữ liệu Nợ/VCSH, Nợ/TS từ nguồn hiện tại.")

            if ic is not None:
                _t = _trend("interest_coverage", periods, hdata)
                _v("Khả năng trả lãi", ic > 5, ic > 2,
                   f"EBIT/Lãi vay = {ic:.1f}x{_t} — rất an toàn",
                   f"EBIT/Lãi vay = {ic:.1f}x{_t} — ổn",
                   f"EBIT/Lãi vay = {ic:.1f}x{_t} — mỏng, cần theo dõi")

        with _nd_col4:
            qr  = latest.get("quick_ratio")
            str_ = latest.get("short_term_ratio")

            if qr is not None:
                _t = _trend("quick_ratio", periods, hdata)
                _v("Thanh khoản nhanh", qr > 1.0, qr > 0.7,
                   f"Quick ratio = {qr:.2f}{_t} — tốt",
                   f"Quick ratio = {qr:.2f}{_t} — ổn",
                   f"Quick ratio = {qr:.2f}{_t} — yếu, rủi ro thanh khoản")
            elif str_ is not None:
                _t = _trend("short_term_ratio", periods, hdata)
                _v("Thanh khoản ngắn hạn", str_ > 1.5, str_ > 1.0,
                   f"Current ratio = {str_:.2f}{_t} — tốt",
                   f"Current ratio = {str_:.2f}{_t} — ổn",
                   f"Current ratio = {str_:.2f}{_t} — yếu")
            else:
                st.info("**Thanh khoản**: Không có dữ liệu Quick ratio, Current ratio từ nguồn hiện tại.")

            if str_ is not None and qr is not None:
                _t = _trend("short_term_ratio", periods, hdata)
                _v("Thanh khoản ngắn hạn", str_ > 1.5, str_ > 1.0,
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
            # Helper: lấy giá trị theo item_id, đổi đơn vị tỷ
            def _v(store, iid, idx=0):
                item = store.get(iid, {})
                vals = item.get("values", [])
                return vals[idx] if idx < len(vals) and vals[idx] is not None else None

            def _fmt(val, unit="tỷ"):
                if val is None: return "—"
                if unit == "tỷ":  return f"{val/1e9:,.1f}"
                if unit == "%":   return f"{val:.1f}%"
                return f"{val:,.1f}"

            # ── Bảng BCTC dạng tab ───────────────────────────────────────
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

                # Lấy 2 kỳ gần nhất để tính tăng trưởng
                rev0   = _v(fs_income,   "isa3",  0)   # Doanh thu thuần kỳ mới nhất
                rev1   = _v(fs_income,   "isa3",  1)
                gp0    = _v(fs_income,   "isa5",  0)   # Lợi nhuận gộp
                ebit0  = _v(fs_income,   "isa11", 0)   # EBIT
                pat0   = _v(fs_income,   "isa20", 0)   # LNST
                pat1   = _v(fs_income,   "isa20", 1)
                int0   = _v(fs_income,   "isa8",  0)   # Chi phí lãi vay
                ta0    = _v(fs_balance,  "bsa1",  0)   # Tài sản ngắn hạn (proxy TS ngắn)
                equity0= None
                # Tìm vốn chủ sở hữu (thường là bse# cuối)
                for iid, item in fs_balance.items():
                    if "vốn chủ" in item["label"].lower() or "equity" in item["label"].lower():
                        v = _v(fs_balance, iid, 0)
                        if v and (equity0 is None or abs(v) > abs(equity0)):
                            equity0 = v
                debt0  = None
                for iid, item in fs_balance.items():
                    if "nợ phải trả" in item["label"].lower() or "total liab" in item["label"].lower():
                        v = _v(fs_balance, iid, 0)
                        if v and (debt0 is None or abs(v) > abs(debt0)):
                            debt0 = v
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
                cfo_q  = _safe_div(cfo0, pat0) if pat0 else None  # Chất lượng lợi nhuận

                # --- Hiển thị KPI phân tích ---
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

                # Tăng trưởng doanh thu
                if rev_g is not None:
                    if rev_g > 20:   st.success(f"✅ **Doanh thu** tăng mạnh {rev_g:+.1f}% so kỳ trước — tín hiệu tích cực.")
                    elif rev_g > 0:  st.info(f"ℹ️ **Doanh thu** tăng nhẹ {rev_g:+.1f}%.")
                    elif rev_g > -10:st.warning(f"⚠️ **Doanh thu** giảm {rev_g:.1f}% — cần theo dõi.")
                    else:            st.error(f"🔴 **Doanh thu** giảm mạnh {rev_g:.1f}% — rủi ro cao.")

                # Biên lợi nhuận
                if gpm is not None:
                    gp = gpm * 100
                    if gp > 30:    st.success(f"✅ **Biên gộp** {gp:.1f}% — rất tốt, có lợi thế cạnh tranh.")
                    elif gp > 15:  st.info(f"ℹ️ **Biên gộp** {gp:.1f}% — ở mức trung bình.")
                    else:          st.warning(f"⚠️ **Biên gộp** {gp:.1f}% — thấp, áp lực chi phí cao.")

                # Tăng trưởng lợi nhuận
                if pat_g is not None:
                    if pat_g > 30:    st.success(f"✅ **LNST** tăng trưởng mạnh {pat_g:+.1f}%.")
                    elif pat_g > 0:   st.info(f"ℹ️ **LNST** tăng {pat_g:+.1f}%.")
                    elif pat_g > -20: st.warning(f"⚠️ **LNST** giảm {pat_g:.1f}% — cần xem xét nguyên nhân.")
                    else:             st.error(f"🔴 **LNST** giảm mạnh {pat_g:.1f}%.")

                # Đòn bẩy
                if de_fs is not None:
                    if de_fs < 0.5:  st.success(f"✅ **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — cấu trúc vốn lành mạnh.")
                    elif de_fs < 1.5:st.info(f"ℹ️ **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — chấp nhận được.")
                    elif de_fs < 3:  st.warning(f"⚠️ **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — khá cao.")
                    else:            st.error(f"🔴 **Đòn bẩy** Nợ/VCSH = {de_fs:.2f}x — rất cao, rủi ro tài chính lớn.")

                # Khả năng trả lãi
                if icr is not None:
                    if icr > 5:    st.success(f"✅ **Khả năng trả lãi** {icr:.1f}x — rất an toàn.")
                    elif icr > 2:  st.info(f"ℹ️ **Khả năng trả lãi** {icr:.1f}x — ổn.")
                    elif icr > 1:  st.warning(f"⚠️ **Khả năng trả lãi** {icr:.1f}x — mỏng, cần chú ý.")
                    else:          st.error(f"🔴 **Khả năng trả lãi** {icr:.1f}x < 1 — nguy hiểm, không đủ trả lãi!")

                # Chất lượng lợi nhuận
                if cfo_q is not None:
                    if cfo_q > 1.0:  st.success(f"✅ **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x — lợi nhuận có thực chất dòng tiền.")
                    elif cfo_q > 0.5:st.info(f"ℹ️ **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x — tương đối ổn.")
                    elif cfo_q >= 0: st.warning(f"⚠️ **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x — LN chưa chuyển hóa thành tiền mặt.")
                    else:            st.error(f"🔴 **Chất lượng LN** CFO/PAT = {cfo_q:.2f}x âm — dòng tiền kinh doanh âm trong khi báo lãi.")

                # Xu hướng đa kỳ nếu đủ dữ liệu
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
                        # Xóa cache cũ để force refresh
                        if ai_cache_key in st.session_state:
                            del st.session_state[ai_cache_key]

                    if ai_cache_key not in st.session_state:
                        with st.spinner("Claude đang phân tích BCTC... (~15-30 giây)"):
                            from vn_invest.analyzer import analyze_bctc
                            _co_name = (overview.get("company_name") or
                                        overview.get("organ_name") or symbol_input)
                            _sector  = (overview.get("industry_name") or
                                        overview.get("sector_vn") or
                                        overview.get("sector") or "")
                            _profile = (overview.get("company_profile") or
                                        overview.get("profile") or
                                        overview.get("business_model") or "")
                            # Debug RSS trước khi gọi AI
                            try:
                                from vn_invest.news_fetcher import search_market_news
                                _rss_debug = search_market_news(symbol_input, _co_name, _sector, max_results=15)
                                st.session_state[f"rss_debug_{symbol_input}"] = _rss_debug
                            except Exception as _e:
                                st.session_state[f"rss_debug_{symbol_input}"] = f"Lỗi: {_e}"

                            # Luôn fetch BCTC quarterly mới nhất cho AI
                            # Không dùng fs_periods từ session (có thể là annual hoặc rỗng)
                            _ai_fs = _fetch_statements(symbol_input, "quarterly")
                            _ai_periods  = _ai_fs.get("periods", [])
                            _ai_income   = _ai_fs.get("income", {})
                            _ai_balance  = _ai_fs.get("balance", {})
                            _ai_cashflow = _ai_fs.get("cashflow", {})

                            # Lấy tin tức thực tế để đưa vào prompt
                            _news        = _fetch_news(symbol_input)
                            _events      = _fetch_events(symbol_input)
                            _shareholders = _fetch_shareholders(symbol_input)
                            # Lấy giá + khối lượng 20 phiên gần nhất
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

                        # ── Nút Phản biện ──────────────────────────────────
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

                                # ── Hỏi thêm / làm rõ ──────────────────────
                                st.markdown("#### 💬 Hỏi thêm về phân tích hoặc phản biện")
                                _fup_key   = f"followup_history_{symbol_input}_{fs_api}"
                                _fup_input = f"followup_input_{symbol_input}_{fs_api}"
                                if _fup_key not in st.session_state:
                                    st.session_state[_fup_key] = []

                                # Hiển thị lịch sử hội thoại
                                for _turn in st.session_state[_fup_key]:
                                    with st.chat_message("user"):
                                        st.markdown(_turn["q"])
                                    with st.chat_message("assistant"):
                                        st.markdown(_turn["a"])

                                _user_q = st.chat_input("Ví dụ: làm rõ phản biện", key=f"chat_{symbol_input}_{fs_api}")
                                if _user_q:
                                    # Build context: phân tích + phản biện + số liệu thô + lịch sử
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
                                    for _t in st.session_state[_fup_key][-4:]:  # giữ 4 turn gần nhất
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

                    # Debug RSS status
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

    # ── Vĩ mô Việt Nam (IMF WEO + tỷ giá spot) ──────────────────────────────
    with st.expander("🌐 Bối cảnh vĩ mô Việt Nam", expanded=False):
        _macro = _fetch_macro()
        if _macro.get("error"):
            st.warning(f"Không tải được dữ liệu vĩ mô: {_macro['error']}")
        else:
            def _latest_m(series):
                return series[0] if series else None

            def _label_yr(item):
                if not item:
                    return "—"
                tag = " (dự báo)" if item.get("is_forecast") else ""
                return f"{item['year']}{tag}"

            _gdp  = _latest_m(_macro.get("gdp_growth", []))
            _cpi  = _latest_m(_macro.get("cpi", []))
            _ca   = _latest_m(_macro.get("current_acct", []))
            _vnd  = _macro.get("usdvnd_spot")

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric(
                f"GDP tăng trưởng ({_label_yr(_gdp)})",
                f"{_gdp['value']:+.2f}%" if _gdp else "—",
            )
            mc2.metric(
                f"Lạm phát CPI ({_label_yr(_cpi)})",
                f"{_cpi['value']:+.2f}%" if _cpi else "—",
            )
            mc3.metric(
                f"Cán cân vãng lai ({_label_yr(_ca)})",
                f"{_ca['value']:+.2f}% GDP" if _ca else "—",
            )
            mc4.metric(
                "USD/VND (spot hôm nay)",
                f"{_vnd:,.0f}" if _vnd else "—",
            )

            # Trend GDP + CPI 5 năm (có đường phân cách dự báo)
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
                    height=220, template="plotly_dark", margin=dict(l=0, r=0, t=10, b=0),
                    legend=dict(orientation="h", y=1.15),
                    yaxis=dict(ticksuffix="%"),
                )
                st.plotly_chart(_fig_m, use_container_width=True)

            st.caption(f"Nguồn: IMF WEO (📌 = dự báo/ước tính năm hiện tại, cập nhật: {_macro['updated']}) | Tỷ giá spot: open.er-api.com")

    st.divider()

    # ── Cơ cấu cổ đông ───────────────────────────────────────────────────────
    st.subheader("👥 Cơ cấu cổ đông")
    with st.spinner("Đang tải cơ cấu cổ đông..."):
        sh_data = _fetch_shareholders(symbol_input)

    sh_summary = sh_data.get("summary", {})
    shareholders = sh_data.get("shareholders", [])
    officers     = sh_data.get("officers", [])
    subsidiaries = sh_data.get("subsidiaries", [])

    # KPI tổng hợp tỷ lệ sở hữu
    if sh_summary:
        mc = sh_summary.get("market_cap")
        sh_c1, sh_c2, sh_c3, sh_c4, sh_c5 = st.columns(5)
        sh_c1.metric("Vốn hóa (tỷ)",
                     f"{mc/1e9:,.0f}" if mc else "—")
        sh_c2.metric("NĐTNN (%)",
                     f"{sh_summary.get('foreign_pct','—')}" if sh_summary.get('foreign_pct') is not None else "—")
        sh_c3.metric("Room NN tối đa (%)",
                     f"{sh_summary.get('foreign_max_pct','—')}" if sh_summary.get('foreign_max_pct') is not None else "—")
        sh_c4.metric("Nhà nước (%)",
                     f"{sh_summary.get('state_pct','—')}" if sh_summary.get('state_pct') is not None else "—")
        sh_c5.metric("Free float (%)",
                     f"{sh_summary.get('free_float_pct','—')}" if sh_summary.get('free_float_pct') is not None else "—")

    sh_tab1, sh_tab2, sh_tab3 = st.tabs(["🏦 Cổ đông lớn", "👔 Ban lãnh đạo", "🏢 Công ty con"])

    with sh_tab1:
        if not shareholders:
            st.info("Không có dữ liệu cổ đông lớn.")
        else:
            df_sh = pd.DataFrame(shareholders)
            # Vẽ pie chart + bảng
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

                label = f"**{title}**" + (f"  \n<small>{meta}</small>" if meta else "")
                with st.expander(f"{date_str}  {title}", expanded=False):
                    if meta:
                        st.caption(meta)
                    body = content or summary
                    if body:
                        st.markdown(body, unsafe_allow_html=True)
                    else:
                        st.caption("Không có nội dung chi tiết.")
                    # Link nguồn hoặc Google search fallback
                    if url:
                        st.markdown(f"🔗 [Xem bài gốc]({url})")
                    else:
                        import urllib.parse
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
                # Chọn các cột phổ biến nếu có
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
                # Đổi tên cột thân thiện
                rename_map = {
                    "date":            "Ngày",
                    "event_type":      "Loại sự kiện",
                    "charter_capital": "Vốn điều lệ (tỷ)",
                    "issue_share":     "Cổ phiếu phát hành",
                    "ratio":           "Tỷ lệ",
                    "notes":           "Ghi chú",
                }
                df_cap = df_cap.rename(columns={k: v for k, v in rename_map.items() if k in df_cap.columns})
                # Chuyển vốn điều lệ sang tỷ đồng nếu đơn vị là đồng
                if "Vốn điều lệ (tỷ)" in df_cap.columns:
                    _cap_col = df_cap["Vốn điều lệ (tỷ)"]
                    if _cap_col.dropna().max() > 1e10:   # đơn vị đồng → chia 1e9
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

# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — KỸ THUẬT
# ═════════════════════════════════════════════════════════════════════════════
with tab_tech:
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
        df_price = _fetch_price(symbol_input, days, source)

    if df_price is None or df_price.empty:
        st.error(f"Không tải được dữ liệu giá cho **{symbol_input}**. Kiểm tra mã hoặc thử nguồn khác.")
    else:
        # Ưu tiên weekly_macd_trend từ Amibroker — Ami dùng full history, vnstock 120d có thể sai chiều
        # Đọc trực tiếp scan_result.csv (không cần đã scan cache trước)
        from vn_invest.screener import get_ami_scan_data as _get_ami_scan_kt, scan_symbol_realtime as _scan_rt
        _ami_kt  = _get_ami_scan_kt().get(symbol_input, {})
        _wmt_ami = _ami_kt.get("ami_wmt")
        if _wmt_ami is not None:
            df_price["weekly_macd_trend"] = int(_wmt_ami)
        sig = get_latest_signals(df_price)

        # ── Realtime vnstock (bar hôm nay từ price_board) ─────────────────
        _rt_sig = None
        if _ami_kt:  # chỉ fetch khi có AMI data để so sánh
            with st.spinner("Đang tải chỉ số realtime..."):
                _rt_sig = _scan_rt(symbol_input)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Giá đóng cửa", f"{sig['close']:,.0f}")
        c2.metric("RSI (14)", f"{sig['rsi']:.1f}")
        c3.metric("Tín hiệu", sig["signal"])
        c4.metric("Rủi ro", sig["risk"])
        c5.metric("Giai đoạn", sig["phase"])

        # ── So sánh AMI (EOD) vs vnstock (realtime) ───────────────────────
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

        # ── Áp lực mua/bán (side_stats) ──────────────────────────────────
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

            # Progress bar tỷ lệ mua/bán
            st.markdown(
                f"<div style='display:flex;height:12px;border-radius:6px;overflow:hidden;margin:4px 0 8px'>"
                f"<div style='width:{_buy_pct}%;background:#00e676'></div>"
                f"<div style='width:{_sell_pct}%;background:#ff1744'></div>"
                f"</div>"
                f"<small style='color:#aaa'>🟢 Mua {_buy_pct:.1f}% &nbsp;|&nbsp; 🔴 Bán {_sell_pct:.1f}%</small>",
                unsafe_allow_html=True,
            )

        st.divider()

        # ── AI Score (LSTM) ───────────────────────────────────────────────
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
            # Lấy 11 bar để tính change_pct, vol_ma20 tính từ toàn bộ df_price
            _cols = [c for c in ["time","open","high","low","close","volume","rsi","volume_ratio"] if c in df_price.columns]
            last10 = df_price.tail(11)[_cols].copy()
            last10 = last10.sort_values("time", ascending=False).reset_index(drop=True)
            last10["change_pct"] = (last10["close"] - last10["close"].shift(-1)) / last10["close"].shift(-1) * 100
            last10 = last10.head(10)

            # Màu nền dòng theo volume_ratio (vol / SMA20_vol)
            def _row_bg(vr):
                if pd.isna(vr):  return ""
                if vr >= 2.5:    return "background:rgba(0,230,118,0.18)"   # xanh đậm — đột biến cực mạnh
                if vr >= 1.75:   return "background:rgba(0,230,118,0.10)"   # xanh nhạt — mạnh
                if vr >= 1.25:   return "background:rgba(255,215,64,0.08)"  # vàng nhạt — cao hơn tb
                if vr < 0.5:     return "background:rgba(255,82,82,0.12)"   # đỏ — volume cạn
                return ""                                                     # bình thường — không tô

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

        # ── Khối ngoại 30 ngày ───────────────────────────────────────────────
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

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

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


# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — QUICK SCAN
# ═════════════════════════════════════════════════════════════════════════════
with tab_scan:
    from datetime import datetime as _dt

    st.header("Quick Scan — Toàn thị trường")

    # ── Session state ─────────────────────────────────────────────────────────
    if "scan_cache" not in st.session_state:
        st.session_state.scan_cache = load_cache()
    if "scan_auto_refresh" not in st.session_state:
        st.session_state.scan_auto_refresh = False
    if "scan_auto_interval" not in st.session_state:
        st.session_state.scan_auto_interval = 10

    # ── Auto-detect scan_result.csv mới từ Amibroker ─────────────────────────
    # Mỗi lần rerun: kiểm tra mtime của scan_result.csv.
    # Nếu mới hơn lần load trước → tự động gọi refresh_signals_from_ami()
    import os as _os_qs
    _ami_scan_path_qs = _os_qs.getenv("AMIBROKER_SCAN_CSV", r"C:\AmibrokerData\scan_result.csv")
    if _os_qs.path.exists(_ami_scan_path_qs):
        _ami_mtime_qs = _os_qs.path.getmtime(_ami_scan_path_qs)
        _last_mtime   = st.session_state.get("scan_ami_mtime", 0)
        if _ami_mtime_qs > _last_mtime:
            # File Amibroker mới hơn → reload signal
            _auto_pb = st.progress(0, text="📡 Phát hiện Amibroker export mới — đang tải...")
            def _auto_cb(i, total, sym):
                _auto_pb.progress(min(i / max(total, 1), 1.0), text=f"Đang load {sym} ({i}/{total})")
            st.session_state.scan_cache = refresh_signals_from_ami(progress_callback=_auto_cb)
            st.session_state.scan_ami_mtime = _ami_mtime_qs
            _auto_pb.empty()
            st.toast(f"✅ Đã tải lại {len(st.session_state.scan_cache)} mã từ Amibroker export mới", icon="📡")

    _ami_list      = get_ami_watchlist()       # từ scan_result.csv (đã lọc qua Amibroker Explorer)
    _all_ami_syms  = get_all_ami_symbols()     # toàn bộ history_by_ticker/*.csv
    _ami_scan_age  = get_ami_scan_age()        # tuổi file scan_result.csv
    _lstm_avail    = model_ready()

    # ── Cảnh báo cache stale: Amibroker Explore đã chạy mới hơn cache ──────────
    try:
        import os as _os
        _ami_scan_path = _os.getenv("AMIBROKER_SCAN_CSV", r"C:\AmibrokerData\scan_result.csv")
        _cache_path = str(_APP_DIR / "data" / "scores_cache.json")
        if _os.path.exists(_ami_scan_path) and _os.path.exists(_cache_path):
            _ami_mtime   = _os.path.getmtime(_ami_scan_path)
            _cache_mtime = _os.path.getmtime(_cache_path)
            _cache_sym_count = len(st.session_state.scan_cache)
            _ami_sym_count   = len(_ami_list)
            _all_sym_count   = len(_all_ami_syms)
            if _ami_mtime > _cache_mtime:
                _diff_min = int((_ami_mtime - _cache_mtime) / 60)
                _diff_label = f"{_diff_min} phút" if _diff_min < 60 else f"{_diff_min//60} giờ {_diff_min%60} phút"
                st.warning(
                    f"⚠️ **Amibroker đã Explore lại** ({_diff_label} trước khi cache được tạo). "
                    f"Cache hiện có **{_cache_sym_count} mã** — "
                    f"Scan đã lọc có **{_ami_sym_count} mã**, Toàn bộ history có **{_all_sym_count} mã**. "
                    f"Nhấn **⚡ Scan** để cập nhật tín hiệu (~20s).",
                    icon="🔁"
                )
    except Exception:
        pass

    # ── Controls row ──────────────────────────────────────────────────────────
    scan_opt_c1, scan_opt_c2, scan_opt_c3, scan_opt_c4, scan_opt_c5 = st.columns([2, 1, 1, 1, 1])
    with scan_opt_c2:
        _use_lstm_scan = st.checkbox(
            "Kèm AI Score", value=_lstm_avail,
            disabled=not _lstm_avail,
            help="Chạy LSTM cho mỗi mã khi scan (~2-3s thêm/mã nếu không có GPU)"
        )
    with scan_opt_c3:
        _live_mode = st.toggle("🔴 Live (vnstock)", value=False, key="scan_live_mode",
                               help="Tính lại RSI/MACD/Dist từ giá vnstock realtime phiên hôm nay. VolRatio giữ từ AMI EOD.")
    with scan_opt_c4:
        _auto_refresh_price = st.toggle("⏱ Tự làm mới giá", value=False, key="scan_auto_toggle")
    with scan_opt_c5:
        _auto_interval_min = st.selectbox("Mỗi (phút)", [5, 10, 15, 30],
                                          index=1, key="scan_interval",
                                          disabled=not _auto_refresh_price)

    col_a, col_b, col_c, col_d = st.columns([2, 2, 2, 2])

    with col_a:
        if st.button("🔄 Làm mới (giá + signal)", use_container_width=True):
            _total_ami = len(load_cache())
            _prog = st.progress(0, text="Đang scan từ Amibroker...")
            def _cb(i, total, sym):
                _prog.progress(min(i / max(total, 1), 1.0), text=f"Scan {sym} ({i}/{total})")
            st.session_state.scan_cache = refresh_signals_from_ami(progress_callback=_cb)
            _prog.empty()
            st.success(f"Đã cập nhật signal + giá cho {len(st.session_state.scan_cache)} mã")

    with col_b:
        _age_note = f" · {_ami_scan_age}" if _ami_scan_age else ""
        _btn_filtered = st.button(
            f"⚡ Scan đã lọc ({len(_ami_list)} mã{_age_note})",
            use_container_width=True,
            help="Scan các mã trong scan_result.csv — đã qua bộ lọc Amibroker Explorer (~20s)",
        )

    with col_c:
        _btn_all = st.button(
            f"🌐 Scan tất cả ({len(_all_ami_syms)} mã)",
            use_container_width=True,
            help="Scan toàn bộ mã có data trong history_by_ticker/ (~20s)",
        )

    # Placeholders đặt NGOÀI button block — Streamlit render ngay khi bắt đầu scan
    # (không đặt trong if button: vì Streamlit batch update cho đến khi script xong)
    _scan_pb_ph   = st.empty()
    _scan_txt_ph  = st.empty()

    if _btn_filtered or _btn_all:
        _syms_to_scan = _ami_list if _btn_filtered else _all_ami_syms
        _total_scan   = len(_syms_to_scan)
        _scan_pb_ph.progress(0)
        _scan_txt_ph.info(f"⏳ Đang scan 0/{_total_scan} mã... (~20 giây, vui lòng chờ)")
        _lock_s = threading.Lock(); _cnt_s = [0]
        def on_progress_scan(i, total, sym):
            with _lock_s:
                _cnt_s[0] += 1
                pct = min(int(_cnt_s[0] / total * 100), 100)
                _scan_pb_ph.progress(pct)
                _scan_txt_ph.info(f"⏳ Scan {sym}... ({_cnt_s[0]}/{total} — {pct}%)")
        st.session_state.scan_cache = scan_ami_watchlist(
            symbols=_syms_to_scan, with_lstm=_use_lstm_scan, progress_callback=on_progress_scan
        )
        _scan_pb_ph.empty()
        ai_note = " (kèm AI Score)" if _use_lstm_scan else ""
        _scan_txt_ph.success(
            f"✅ Hoàn tất! Scan {len(st.session_state.scan_cache)}/{_total_scan} mã{ai_note}."
        )
        st.rerun()

    with col_d:
        scan_single = st.text_input("Scan 1 mã", placeholder="VNM", key="scan_single_input").upper().strip()
        if st.button("Scan mã này", use_container_width=True) and scan_single:
            with st.spinner(f"Đang scan {scan_single}..."):
                rec = scan_ami_symbol(scan_single, with_lstm=_use_lstm_scan) or scan_symbol(scan_single, source=source)
            if rec: st.session_state["single_scan_result"] = rec
            else:   st.error("Không lấy được dữ liệu")

    # ── Fix 2+3: Hiển thị tuổi cache + cảnh báo signal cũ ───────────────────
    _meta = load_cache_meta()
    _scanned_str   = _meta.get("scanned_at")
    _refreshed_str = _meta.get("price_refreshed_at")

    def _age_label(ts_str: str | None) -> str:
        if not ts_str:
            return "chưa có"
        try:
            dt   = _dt.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            mins = int((_dt.now() - dt).total_seconds() / 60)
            if mins < 1:   return "vừa xong"
            if mins < 60:  return f"{mins} phút trước"
            hrs = mins // 60
            if hrs < 24:   return f"{hrs} giờ trước"
            return f"{hrs // 24} ngày trước"
        except Exception:
            return ts_str

    _info_parts = [f"📊 Signal scan: **{_age_label(_scanned_str)}**"]
    if _refreshed_str:
        _info_parts.append(f"⚡ Giá làm mới: **{_age_label(_refreshed_str)}**")
    if _meta.get("count"):
        _info_parts.append(f"**{_meta['count']} mã**")
    st.caption(" &nbsp;|&nbsp; ".join(_info_parts))

    # Cảnh báo khi giá mới hơn signal scan
    if _scanned_str and _refreshed_str:
        try:
            _t_scan    = _dt.strptime(_scanned_str,   "%Y-%m-%d %H:%M:%S")
            _t_refresh = _dt.strptime(_refreshed_str, "%Y-%m-%d %H:%M:%S")
            if _t_refresh > _t_scan:
                st.warning(
                    "⚠️ **Giá đã được làm mới** sau lần Scan cuối — "
                    "cột Giá phản ánh giá hiện tại nhưng **Signal / RSI / MACD vẫn từ lần Scan trước**. "
                    "Nhấn '⚡ Scan Amibroker' để tính lại tín hiệu."
                )
        except Exception:
            pass

    if "single_scan_result" in st.session_state:
        rec = st.session_state["single_scan_result"]
        st.divider()
        _si = {"BUY-A":"🟢","BUY-B":"🟩","HOLD":"🟡","SELL-B":"🟠","SELL-A":"🔴"}
        _ri = {"Low":"🟢","Medium":"🟡","High":"🔴"}
        _pi = {"Accumulation":"📦","Markup":"📈","Distribution":"📤","Markdown":"📉","Neutral":"➖"}
        sig_s = rec.get("signal",""); risk_s = rec.get("risk",""); phase_s = rec.get("phase","")
        st.markdown(f"### {_si.get(sig_s,'')} Kết quả scan — **{rec['symbol']}**")
        r1c1,r1c2,r1c3,r1c4 = st.columns(4)
        r1c1.metric("Giá đóng cửa",   f"{rec.get('close',0):,.2f}")
        r1c2.metric("RSI (14)",        f"{rec.get('rsi',0):.1f}")
        r1c3.metric("Điểm kỹ thuật",  f"{rec.get('tech_score',0):.1f} / 100")
        r1c4.metric("Dist EMA34",      f"{rec.get('dist_ema34_pct',0):.2f}%")
        r2c1,r2c2,r2c3,r2c4 = st.columns(4)
        r2c1.metric("Tín hiệu",  f"{_si.get(sig_s,'')} {sig_s}")
        r2c2.metric("Rủi ro",    f"{_ri.get(risk_s,'')} {risk_s}")
        r2c3.metric("Giai đoạn", f"{_pi.get(phase_s,'')} {phase_s}")
        r2c4.metric("MACD Hist", f"{rec.get('macd_hist',0):.4f}")

    cache_data = st.session_state.scan_cache
    st.divider()

    filter_cols = st.columns(5)
    f_signal   = filter_cols[0].selectbox("Tín hiệu Python", ["Tất cả","BUY-A","BUY-B","HOLD","SELL-B","SELL-A"])
    f_risk     = filter_cols[1].selectbox("Rủi ro",          ["Tất cả","Low","Medium","High"])
    f_phase    = filter_cols[2].selectbox("Giai đoạn",       ["Tất cả","Accumulation","Markup","Distribution","Markdown","Neutral"])
    f_ai       = filter_cols[3].selectbox("AI Score",        ["Tất cả","≥ 70 (Mạnh)","≥ 50 (Tích cực)","≤ 30 (Yếu)","Có AI Score"])
    f_ami_rec  = filter_cols[4].selectbox("Ami Rec",         ["Tất cả","STRONG BUY","ACCUMULATE","WATCHING","RISK SELL","TOP SELL"])

    filter_cols2 = st.columns(3)
    f_setup    = filter_cols2[0].selectbox("Setup (Ami)", ["Tất cả","FLAT BASE","VCP TIGHT","PKT PIVOT","PULLBACK","PWR-PLAY","GAP UP"])
    f_forecast = filter_cols2[1].selectbox("Forecast (Ami)", ["Tất cả","BULL DIV","BEAR DIV","BB BOT REV"])
    f_pattern  = filter_cols2[2].selectbox("Mẫu hình giá", ["Tất cả","Có mẫu bull","Có mẫu bear","Có mẫu neutral","Không có mẫu"])

    # Checkbox hiện mã bị hạn chế (ẩn mặc định)
    show_restricted = st.checkbox("Hiện mã bị hạn chế/cảnh báo", value=False,
                                  help="Mặc định ẩn mã restricted/suspended/warning khỏi kết quả")

    # ── Live mode: cập nhật nhanh từ price_board batch (không fetch history) ─
    if _live_mode:
        if "live_cache" not in st.session_state or st.session_state.get("live_cache_base") != id(st.session_state.scan_cache):
            from vn_invest.screener import apply_live_bar_to_cache as _apply_live
            from vnstock import Trading as _Trading

            _live_syms = [r["symbol"] for r in st.session_state.scan_cache]
            _live_pb   = st.progress(0, text="🔴 Đang lấy giá realtime (batch)...")
            try:
                _t_board  = _Trading(source="KBS", symbol="VNI")
                _board_df = _t_board.price_board(symbols_list=_live_syms)
                _live_pb.progress(0.7, text="🔴 Đang tính RSI/Dist/Signal...")
                _merged   = _apply_live(st.session_state.scan_cache, _board_df)
                _n_ok     = sum(1 for r in _merged if r.get("live_updated"))
            except Exception:
                _merged = list(st.session_state.scan_cache)
                _n_ok   = 0
            _live_pb.empty()
            st.session_state["live_cache"]      = _merged
            st.session_state["live_cache_base"] = id(st.session_state.scan_cache)
            st.caption(f"🔴 Live: {_n_ok}/{len(_live_syms)} mã · RSI/Dist/Signal xấp xỉ realtime · VolRatio giữ AMI EOD")

        _active_cache = st.session_state["live_cache"]
        st.info("🔴 **Live mode** — RSI/MACD/Dist/Signal từ vnstock realtime · VolRatio giữ từ AMI EOD")
    else:
        _active_cache = st.session_state.scan_cache

    filtered = filter_cache(
        signal=None   if f_signal=="Tất cả"   else f_signal,
        risk=None     if f_risk=="Tất cả"     else f_risk,
        phase=None    if f_phase=="Tất cả"    else f_phase,
        ai_score=None if f_ai=="Tất cả"       else f_ai,
        ami_rec=None  if f_ami_rec=="Tất cả"  else f_ami_rec,
        setup=None    if f_setup=="Tất cả"    else f_setup,
        forecast=None if f_forecast=="Tất cả" else f_forecast,
        pattern=None  if f_pattern=="Tất cả"  else f_pattern,
        data=_active_cache,
        exclude_restricted=not show_restricted,
    )

    # Toàn bộ cache từ session_state (không đọc disk nữa)
    _full_cache = _active_cache
    _df_full    = pd.DataFrame(_active_cache) if _active_cache else pd.DataFrame()
    _has_ai_full = "ai_score" in _df_full.columns and _df_full["ai_score"].notna().any() if not _df_full.empty else False

    # ── Khuyến nghị nhanh (luôn từ full cache, không phụ thuộc filter) ─────────
    if not _df_full.empty:
        st.subheader("⚡ Khuyến Nghị Nhanh")

        _BAD_PHASES  = {"Distribution", "Markdown"}
        _BUY_SIGNALS = {"BUY-A", "BUY-B"}
        _DOWNTREND_PHASES = {"Markdown"}   # downtrend rõ → chặn mọi khuyến nghị mua

        # Mẫu bearish mạnh (Bulkowski ≥ 70%) → chặn Mua mạnh
        _STRONG_BEAR_PATTERNS = {
            "three black crows", "evening star", "bearish engulfing",
            "head & shoulders", "double top", "descending triangle",
            "shooting star", "hanging man",
        }

        def _has_strong_bear(row) -> bool:
            """Kiểm tra cache có mẫu bearish mạnh không."""
            pats = " ".join([
                str(row.get("candle_patterns") or ""),
                str(row.get("chart_patterns")  or ""),
            ]).lower()
            return any(p in pats for p in _STRONG_BEAR_PATTERNS)

        def _rsi_ok_for_buy(rsi: float) -> bool:
            """RSI hợp lệ để xét BUY:
            - RSI ≤ 30: oversold → đảo chiều tăng tiềm năng ✅
            - RSI 31–40: đang giảm, chưa đảo chiều ❌
            - RSI 41–69: momentum bình thường ✅
            - RSI ≥ 70: overbought → đảo chiều xuống tiềm năng ❌
            """
            return rsi <= 30 or (41 <= rsi < 70)

        def _con_label(row):
            ai    = row.get("ai_score")
            kt    = float(row.get("tech_score", 50) or 50)
            risk  = row.get("risk",   "Medium") or "Medium"
            phase = row.get("phase",  "Neutral") or "Neutral"
            sig   = row.get("signal", "HOLD") or "HOLD"
            dist  = float(row.get("dist_ema34_pct", 0) or 0)
            rsi   = float(row.get("rsi", 50) or 50)
            has_ai = ai is not None and not (isinstance(ai, float) and pd.isna(ai))

            # Điều kiện 1: MACD phải dương cho "Tích cực" trở lên
            macd_pos = float(row.get("macd_hist", 0) or 0) > 0

            # Điều kiện 2: RSI hợp lệ (oversold đảo chiều hoặc vùng bình thường)
            rsi_ok = _rsi_ok_for_buy(rsi)

            # Điều kiện 3: xu hướng 3–5 phiên (ít nhất 1 trong 2 thoả)
            trend_ok = bool(row.get("macd_rising")) or bool(row.get("price_above_sma5_3d"))

            # Điều kiện 4: xu hướng dài hạn — chặn downtrend nhiều tuần
            wmt       = int(row.get("weekly_macd_trend") or 0)
            ma_al     = int(row.get("ma_aligned") or 0)
            trend20   = float(row.get("price_trend_20d") or 0)
            # weekly_macd âm = downtrend nhiều tuần; ma_aligned âm = MA bearish stacked
            longterm_ok = (wmt >= 0) and (ma_al >= 0)
            # Cho phép wmt=-1 nếu MA aligned dương mạnh (đang phục hồi sau correction)
            longterm_ok_relaxed = (wmt >= 0) or (ma_al > 0 and trend20 > -5)

            # Đảo chiều: reversal engine phát hiện tín hiệu đủ mạnh
            # Fix #4: ngưỡng 40 (không phải 55) — 1 tín hiệu divergence (68%×0.80=54%) vẫn lọt qua
            # Fix #5: bearish reversal → cờ riêng để tích hợp vào Thận trọng
            rev_type     = row.get("reversal_type", "none") or "none"
            _rs = row.get("reversal_strength", 0)
            rev_strength = 0 if pd.isna(_rs) else int(_rs or 0)
            is_bull_rev  = rev_type == "bullish"  and rev_strength >= 40
            is_bear_rev  = rev_type == "bearish"  and rev_strength >= 40

            # Chặn downtrend rõ: Markdown phase hoặc ma_aligned âm
            in_downtrend = phase in _DOWNTREND_PHASES or ma_al < 0

            # Chặn: mẫu bearish mạnh, dist quá âm, downtrend dài hạn
            bear_block = _has_strong_bear(row) or dist < -20 or not longterm_ok_relaxed or in_downtrend
            buy_ok     = macd_pos and rsi_ok and trend_ok and not bear_block

            if has_ai:
                ai = float(ai)
                # Mua mạnh: longterm_ok chặt: wmt>=0 VÀ ma_al>0 (không cho ma_al=0)
                if (ai >= 70 and kt >= 60
                        and risk != "High"
                        and phase not in _BAD_PHASES
                        and sig in _BUY_SIGNALS
                        and buy_ok
                        and wmt >= 0 and ma_al > 0):
                    return "✅ Mua mạnh"
                if ai >= 50 and kt >= 50 and buy_ok:              return "🟢 Tích cực"
                if is_bull_rev and not bear_block and risk != "High": return "🔄 Đảo Chiều"
                if ai <= 30 and kt <= 35:                          return "🔴 Bán"
                if ai <= 40 and kt <= 45:                          return "🟠 Thận trọng"
                if bear_block or is_bear_rev:                      return "🟠 Thận trọng"
                if ai >= 70 and kt < 50:                           return "⚠️ AI↑ KT↓"
                if ai < 40  and kt >= 60:                          return "⚠️ AI↓ KT↑"
                return "🟡 Trung tính"
            else:
                # Mua mạnh: wmt>=0 VÀ ma_al>0 bắt buộc
                if (sig == "BUY-A" and risk != "High"
                        and phase not in _BAD_PHASES
                        and buy_ok
                        and wmt >= 0 and ma_al > 0):
                    return "✅ Mua mạnh"
                if sig in ("BUY-A", "BUY-B") and phase not in _BAD_PHASES and buy_ok:
                    return "🟢 Tích cực"
                if is_bull_rev and not bear_block and risk != "High": return "🔄 Đảo Chiều"
                if sig in ("SELL-A", "SELL-B"):                    return "🔴 Bán"
                if risk == "High" or bear_block or is_bear_rev:    return "🟠 Thận trọng"
                return "🟡 Trung tính"

        # Loại mã bị hạn chế khỏi khuyến nghị nhanh (blacklist tĩnh + stock_status động)
        _mask_restricted = _df_full["symbol"].isin(RESTRICTED_SYMBOLS)
        if "stock_status" in _df_full.columns:
            _mask_restricted |= _df_full["stock_status"].isin(_BAD_STATUSES)
        _df_rec = _df_full[~_mask_restricted].copy()

        # Cache _con_label theo id(scan_cache) — tránh tính lại khi chỉ đổi filter
        _rec_cache_key = id(st.session_state.scan_cache)
        if st.session_state.get("_rec_label_key") != _rec_cache_key:
            _df_rec["_con"] = _df_rec.apply(_con_label, axis=1)
            st.session_state["_rec_label_cache"] = dict(zip(_df_rec["symbol"], _df_rec["_con"]))
            st.session_state["_rec_label_key"]   = _rec_cache_key
        else:
            _df_rec["_con"] = _df_rec["symbol"].map(st.session_state["_rec_label_cache"]).fillna("🟡 Trung tính")
        _sort_col = "ai_score" if "ai_score" in _df_rec.columns else "tech_score"
        _buy_strong = _df_rec[_df_rec["_con"] == "✅ Mua mạnh"].nlargest(5, _sort_col)
        _buy_pos    = _df_rec[_df_rec["_con"] == "🟢 Tích cực"].nlargest(5, _sort_col)
        _reversal   = _df_rec[_df_rec["_con"] == "🔄 Đảo Chiều"].nlargest(5, "reversal_strength") \
                      if "reversal_strength" in _df_rec.columns else pd.DataFrame()
        _sell       = _df_rec[_df_rec["_con"] == "🔴 Bán"].nsmallest(5, _sort_col)
        _warning    = _df_rec[_df_rec["_con"].isin(["⚠️ AI↑ KT↓","⚠️ AI↓ KT↑","🟠 Thận trọng"])].nlargest(5, _sort_col)

        def _rec_cards(df_grp, bg_color, label):
            if df_grp.empty:
                st.caption(f"_Không có mã {label}_")
                return
            cols = st.columns(min(len(df_grp), 5))
            for col, (_, row) in zip(cols, df_grp.iterrows()):
                def _fv(v, default=0.0):
                    """Safe float — trả default nếu None/NaN."""
                    try:
                        f = float(v)
                        return f if f == f else default  # NaN check
                    except (TypeError, ValueError):
                        return default

                sym        = row["symbol"]
                ai         = _fv(row.get("ai_score"))
                kt         = _fv(row.get("tech_score"))
                close      = _fv(row.get("close"))
                rsi        = _fv(row.get("rsi"))
                dist       = _fv(row.get("dist_ema34_pct"))
                phase      = str(row.get("phase") or "")
                risk_s     = str(row.get("risk")  or "")
                candle_pats = str(row.get("candle_patterns") or "")
                candle_tf   = str(row.get("candle_timeframe") or "none")
                chart_pats  = str(row.get("chart_patterns")  or "")
                reason      = str(row.get("reason") or "")

                _ps     = {"Accumulation":"Tích lũy","Markup":"Tăng","Distribution":"Phân phối",
                           "Markdown":"Giảm","Neutral":"Trung tính"}.get(phase, phase)
                _ri_col = {"Low":"#00e676","Medium":"#ffd740","High":"#ff1744"}.get(risk_s,"#aaaaaa")
                _ai_lbl = f"{ai:.0f}" if ai > 0 else "—"

                # Mẫu nến: phân biệt daily (vàng) vs weekly (cam, kém tin hơn)
                if candle_pats:
                    if candle_tf == "weekly":
                        _cpat_lbl  = "Nến [Tuần — tin cậy thấp hơn daily]"
                        _cpat_color = "#ffb74d"  # cam
                    else:
                        _cpat_lbl  = "Nến [Daily]"
                        _cpat_color = "#ffe082"  # vàng
                    _cpat_html = (f'<div style="font-size:0.76em;color:{_cpat_color};margin-top:3px">'
                                  f'{_cpat_lbl}: {candle_pats}</div>')
                else:
                    _cpat_html = ""

                _gpat_html = (f'<div style="font-size:0.76em;color:#80cbc4;margin-top:2px">'
                              f'Chart: {chart_pats}</div>') if chart_pats else ""
                _bullets   = [b for b in reason.split(" • ") if b.strip()] if reason else []
                _rea_short = " • ".join(_bullets[:2])
                _rea_html  = (f'<div style="font-size:0.74em;color:#b0bec5;margin-top:3px;line-height:1.45">'
                              f'{_rea_short}</div>') if _rea_short else ""

                col.markdown(f"""
<div style="background:{bg_color};border-radius:10px;padding:12px 14px;margin:2px 0;color:#ffffff">
<b style="font-size:1.1em">{sym}</b><br>
<span style="font-size:0.85em;color:#cccccc">{close:,.0f} &nbsp;|&nbsp; RSI {rsi:.0f}</span><br>
<span style="font-size:0.82em;color:#aaaaaa">Dist {dist:+.1f}% &nbsp;·&nbsp; {_ps}</span>
{_cpat_html}{_gpat_html}
<hr style="margin:5px 0;border-color:rgba(255,255,255,0.15)">
<span>🤖 AI <b>{_ai_lbl}</b> &nbsp; KT <b>{kt:.0f}</b></span>
&nbsp;<span style="font-size:0.8em;color:{_ri_col}">● {risk_s}</span>
{_rea_html}
</div>""", unsafe_allow_html=True)
                if _bullets:
                    with col.expander("Chi tiet phan tich"):
                        for bullet in _bullets:
                            st.markdown(f"- {bullet}")

        kn_t1, kn_t2, kn_t3, kn_t4, kn_t5 = st.tabs([
            f"✅ Mua mạnh ({len(_buy_strong)})",
            f"🟢 Tích cực ({len(_buy_pos)})",
            f"🔄 Đảo Chiều ({len(_reversal)})",
            f"🔴 Bán ({len(_sell)})",
            f"⚠️ Thận trọng ({len(_warning)})",
        ])
        with kn_t1: _rec_cards(_buy_strong, "#1a3a2a", "Mua mạnh")
        with kn_t2: _rec_cards(_buy_pos,    "#1a2e1a", "Tích cực")
        with kn_t3:
            if not _reversal.empty:
                st.caption("⚠️ Tín hiệu đảo chiều tiềm năng — chưa xác nhận, rủi ro cao hơn Mua mạnh. Chỉ tham khảo.")
            _rec_cards(_reversal, "#1a2535", "Đảo Chiều")
        with kn_t4: _rec_cards(_sell,        "#3a1a1a", "Bán")
        with kn_t5: _rec_cards(_warning,     "#2a2a1a", "Thận trọng")

        st.divider()

    # ── Bảng chi tiết (theo filter) ──────────────────────────────────────────
    if not filtered:
        st.info("Cache rỗng. Nhấn 'Scan Amibroker' để bắt đầu.")
    else:
        df_scan = pd.DataFrame(filtered)
        has_ai  = "ai_score" in df_scan.columns and df_scan["ai_score"].notna().any()

        # Lọc AI Score
        if has_ai and f_ai != "Tất cả":
            if f_ai == "≥ 70 (Mạnh)":      df_scan = df_scan[df_scan["ai_score"] >= 70]
            elif f_ai == "≥ 50 (Tích cực)": df_scan = df_scan[df_scan["ai_score"] >= 50]
            elif f_ai == "≤ 30 (Yếu)":      df_scan = df_scan[df_scan["ai_score"] <= 30]
            elif f_ai == "Có AI Score":      df_scan = df_scan[df_scan["ai_score"].notna()]

        # Lọc Ami Rec (từ Amibroker scan_result.csv)
        if f_ami_rec != "Tất cả" and "ami_rec_label" in df_scan.columns:
            df_scan = df_scan[df_scan["ami_rec_label"] == f_ami_rec]

        # Lọc Setup
        if f_setup != "Tất cả" and "ami_setup" in df_scan.columns:
            df_scan = df_scan[df_scan["ami_setup"] == f_setup].copy()

        # Lọc Forecast
        if f_forecast != "Tất cả" and "ami_forecast" in df_scan.columns:
            df_scan = df_scan[df_scan["ami_forecast"] == f_forecast].copy()

        # Lọc Mẫu hình giá
        if f_pattern != "Tất cả" and "chart_patterns" in df_scan.columns:
            cp = df_scan["chart_patterns"].fillna("")
            if f_pattern == "Có mẫu bull":
                df_scan = df_scan[cp.str.contains(r"\[bull,", na=False)].copy()
            elif f_pattern == "Có mẫu bear":
                df_scan = df_scan[cp.str.contains(r"\[bear,", na=False)].copy()
            elif f_pattern == "Có mẫu neutral":
                df_scan = df_scan[cp.str.contains(r"\[neutral,", na=False)].copy()
            elif f_pattern == "Không có mẫu":
                df_scan = df_scan[cp.eq("")].copy()

        # Đếm mã restricted đang bị ẩn
        _all_rows = st.session_state.scan_cache
        _n_restricted = sum(
            1 for r in _all_rows
            if r.get("stock_status", "normal") in _BAD_STATUSES
        ) if not show_restricted else 0
        if _n_restricted:
            _restricted_names = ", ".join(
                r["symbol"] for r in _all_rows
                if r.get("stock_status", "normal") in _BAD_STATUSES
            )
            st.warning(
                f"⛔ Đã ẩn **{_n_restricted} mã** bị hạn chế/cảnh báo khỏi kết quả: "
                f"`{_restricted_names}` — tick 'Hiện mã bị hạn chế' để xem."
            )

        if not has_ai and model_ready():
            st.caption(f"Hiển thị {len(df_scan)} mã — tick 'Kèm AI Score' rồi Scan lại để lọc theo AI Score")
        else:
            st.caption(f"Hiển thị {len(df_scan)} mã" + (" | AI Score: LSTM" if has_ai else ""))

        # Cột Đồng thuận — dùng list comprehension tránh pandas apply quirks
        df_scan = df_scan.reset_index(drop=True).copy()
        df_scan["consensus"] = [_con_label(df_scan.iloc[_i]) for _i in range(len(df_scan))]

        # Lớp 3: badge cảnh báo cho mã bị hạn chế (hiện khi show_restricted=True)
        _STATUS_BADGE = {"restricted": "⛔ Hạn chế", "suspended": "🚫 Tạm ngừng",
                         "warning": "⚠️ Cảnh báo", "delisted": "❌ Hủy niêm yết"}
        if "stock_status" in df_scan.columns:
            df_scan["_warn"] = df_scan["stock_status"].map(
                lambda s: _STATUS_BADGE.get(s, "") if pd.notna(s) else ""
            )
        else:
            df_scan["_warn"] = ""

        display_cols = [c for c in [
            "symbol","_warn","close","ami_date","rsi","dist_ema34_pct",
            "atr_pct","bb_width_pct","volume_ratio",
            "ai_score","tech_score","consensus","signal","risk","phase",
            "ami_rec_label","ami_score","ami_setup","ami_forecast",
            "chart_patterns",
        ] if c in df_scan.columns]
        df_display = df_scan[display_cols].rename(columns={
            "symbol":"Mã","_warn":"Trạng thái","close":"Giá","ami_date":"Ngày DL",
            "rsi":"RSI","dist_ema34_pct":"Dist%",
            "atr_pct":"ATR%","bb_width_pct":"BB%","volume_ratio":"VolR",
            "ai_score":"AI","tech_score":"KT","consensus":"Đồng thuận",
            "signal":"Tín hiệu","risk":"Rủi ro","phase":"Giai đoạn",
            "ami_rec_label":"Ami Rec","ami_score":"AmiSc",
            "ami_setup":"Setup","ami_forecast":"Forecast",
            "chart_patterns":"Mẫu hình giá",
        })
        # Ẩn cột Trạng thái nếu không có mã nào bị đánh dấu
        if df_display["Trạng thái"].eq("").all():
            df_display = df_display.drop(columns=["Trạng thái"])
        st.markdown("""<style>
[data-testid="stDataFrame"] .ag-cell {
    white-space: normal !important;
    word-break: break-word !important;
    line-height: 1.5 !important;
}
</style>""", unsafe_allow_html=True)
        st.dataframe(df_display, use_container_width=True, hide_index=True,
            column_config={
                "Mã":        st.column_config.TextColumn(width="small"),
                "Giá":       st.column_config.NumberColumn(format="%,.2f", width="small"),
                "Ngày DL":   st.column_config.TextColumn(width="small",
                                 help="Ngày dữ liệu từ Amibroker (DD/MM/YYYY)"),
                "RSI":       st.column_config.NumberColumn(format="%.1f",  width="small"),
                "Dist%":     st.column_config.NumberColumn(format="%.1f%%", width="small",
                                 help="Dist EMA34%"),
                "ATR%":      st.column_config.NumberColumn(format="%.1f%%", width="small",
                                 help="ATR/Giá — volatility thực tế. >3%: biến động cao"),
                "BB%":       st.column_config.NumberColumn(format="%.1f%%", width="small",
                                 help="Bollinger Band Width. <5%: squeeze, >15%: đang giãn"),
                "VolR":      st.column_config.NumberColumn(format="%.2f",   width="small",
                                 help="Khối lượng / SMA20(KL). >1.5: xác nhận tín hiệu mạnh"),
                "KT":        st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "AI":        st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Đồng thuận":st.column_config.TextColumn(width="small"),
                "Tín hiệu":  st.column_config.TextColumn(width="small"),
                "Rủi ro":    st.column_config.TextColumn(width="small"),
                "Giai đoạn": st.column_config.TextColumn(width="small"),
                "Ami Rec":   st.column_config.TextColumn(width="small"),
                "AmiSc":     st.column_config.NumberColumn(format="%.0f", width="small"),
                "Setup":     st.column_config.TextColumn(width="small"),
                "Forecast":  st.column_config.TextColumn(width="small"),
                "Mẫu hình giá": st.column_config.TextColumn(width="large"),
            })

        # ── PyGWalker — Phân tích nâng cao ──────────────────────────────────
        with st.expander("🔬 Phân tích nâng cao (kéo-thả như Tableau)", expanded=False):
            try:
                from pygwalker.api.streamlit import StreamlitRenderer

                @st.cache_resource
                def _get_pyg_walker(data_hash: int, cols: tuple):
                    return StreamlitRenderer(
                        df_scan[list(cols)],
                        kernel_computation=True,
                        appearance="dark",
                    )

                _pyg_cols = tuple(df_scan.columns.tolist())
                _pyg_hash = hash(_pyg_cols + (len(df_scan),))
                _get_pyg_walker(_pyg_hash, _pyg_cols).explorer(default_tab="data")
            except ImportError:
                st.warning("Cài `pygwalker` để dùng tính năng này: `pip install pygwalker`")
            except Exception as e:
                st.error(f"PyGWalker lỗi: {e}")

        st.divider()
        st.subheader("Phân bổ tín hiệu")
        if "signal" in df_scan.columns:
            sig_count = df_scan["signal"].value_counts().reset_index()
            sig_count.columns = ["Tín hiệu","Số mã"]
            st.bar_chart(sig_count.set_index("Tín hiệu"), use_container_width=True)

        # ── Backtest kết quả ─────────────────────────────────────────────────
        st.divider()
        st.subheader("📊 Backtest — Win rate tín hiệu trên dữ liệu VN")
        from vn_invest.backtester import load_results as _load_bt, run_backtest as _run_bt

        _bt_data = _load_bt()
        _bt_fwd  = st.selectbox("Kỳ kiểm định (ngày giao dịch)",
                                 [5, 10, 20], index=2, key="bt_fwd")
        _bt_maxs = st.slider("Số mã backtest tối đa", 50, 500, 200, step=50, key="bt_maxs")

        if _bt_data:
            _bt_meta = f"Đã backtest {_bt_data['symbols_scanned']} mã · "           \
                       f"{_bt_data['total_signals']:,} tín hiệu · "                 \
                       f"T+{_bt_data['forward_days']} · {_bt_data['computed_at']}"
            st.caption(_bt_meta)

            # Alpha (BUY-A avg − market avg) — metric chính cho VN market
            # Signal Edge (BUY-A − SELL-A) thường âm trong VN do upward bias + mean reversion
            _alpha  = _bt_data.get("buy_a_alpha")
            _mkt    = _bt_data.get("market_avg_return")
            _edge   = _bt_data.get("signal_edge")
            _filters = _bt_data.get("filters_applied", [])
            _edge_col, _filter_col = st.columns([1, 2])
            with _edge_col:
                if _alpha is not None:
                    _ac = "#00e676" if _alpha > 2 else ("#ffd740" if _alpha > 0 else "#ff5252")
                    _mkt_str = f" | Mkt avg: {_mkt:+.1f}%" if _mkt is not None else ""
                    _edge_str = f" | Edge(BUY-A−SELL-A): {_edge:+.1f}%" if _edge is not None else ""
                    st.markdown(
                        f"""<div style="background:#1e2130;border-left:4px solid {_ac};
                        border-radius:6px;padding:8px 12px">
                        <span style="color:#aaa;font-size:0.8em">BUY-A Alpha (vs market avg)</span><br>
                        <span style="color:{_ac};font-size:1.3em;font-weight:700">{_alpha:+.1f}%</span>
                        <span style="color:#888;font-size:0.75em"> · &gt;2% = BUY-A vượt trội "mua bừa"{_mkt_str}{_edge_str}</span>
                        </div>""", unsafe_allow_html=True)
            with _filter_col:
                if _filters:
                    st.markdown(
                        f"""<div style="background:#1e2130;border-radius:6px;padding:8px 12px">
                        <span style="color:#aaa;font-size:0.8em">Filters: </span>
                        <span style="color:#69f0ae;font-size:0.8em">{' · '.join(_filters)}</span>
                        </div>""", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            _SIG_ORDER = ["BUY-A","BUY-B","HOLD","SELL-B","SELL-A"]
            _SIG_COLOR = {"BUY-A":"#00e676","BUY-B":"#69f0ae",
                          "HOLD":"#ffd740","SELL-B":"#ff9100","SELL-A":"#ff5252"}
            _WIN_LABEL = {"BUY-A":"giá tăng","BUY-B":"giá tăng",
                          "HOLD":"giá ±ngưỡng","SELL-B":"giá giảm","SELL-A":"giá giảm"}
            _bt_cols = st.columns(5)
            for _bi, _sig in enumerate(_SIG_ORDER):
                _s = _bt_data["summary"].get(_sig, {})
                _cnt = _s.get("count", 0)
                _wr  = _s.get("win_rate")
                _avg = _s.get("avg_return")
                _std = _s.get("std_return")
                _clr = _SIG_COLOR[_sig]
                _wlbl = _WIN_LABEL[_sig]
                _bt_cols[_bi].markdown(
                    f"""<div style="background:#1e2130;border-left:4px solid {_clr};
                    border-radius:6px;padding:10px 12px;text-align:center">
                    <div style="color:{_clr};font-weight:700;font-size:0.9em">{_sig}</div>
                    <div style="font-size:1.4em;font-weight:700;color:#fff;margin:4px 0">
                      {"—" if _wr is None else f"{_wr:.0f}%"}</div>
                    <div style="font-size:0.72em;color:#888">win ({_wlbl})</div>
                    <div style="font-size:0.8em;color:#aaa;margin-top:5px">
                      avg <b>{("—" if _avg is None else f"{_avg:+.1f}%")}</b></div>
                    <div style="font-size:0.72em;color:#666">
                      σ {("—" if _std is None else f"{_std:.1f}%")} · {_cnt:,} tín hiệu</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Chưa có kết quả backtest. Nhấn nút bên dưới để chạy lần đầu (~2-5 phút).")

        if st.button("▶ Chạy Backtest", use_container_width=True, key="run_bt"):
            _bt_pb = st.progress(0)
            _bt_txt = st.empty()
            def _bt_cb(i, total, sym):
                pct = int(i / max(total, 1) * 100)
                _bt_pb.progress(pct)
                _bt_txt.caption(f"Backtest {sym}... ({i}/{total})")
            with st.spinner("Đang chạy backtest..."):
                _bt_result = _run_bt(forward_days=_bt_fwd, max_symbols=_bt_maxs,
                                     progress_callback=_bt_cb)
            _bt_pb.empty(); _bt_txt.empty()
            st.success(f"Hoàn thành! {_bt_result['total_signals']:,} tín hiệu "
                       f"từ {_bt_result['symbols_scanned']} mã.")
            st.rerun()

    # ── Fix 4: Auto price-refresh ─────────────────────────────────────────────
    if _auto_refresh_price:
        import time as _time
        _interval_secs = _auto_interval_min * 60
        # Lưu thời điểm refresh cuối vào session_state
        if "scan_last_auto_refresh" not in st.session_state:
            st.session_state.scan_last_auto_refresh = 0.0
        _now_ts  = _time.time()
        _elapsed = _now_ts - st.session_state.scan_last_auto_refresh
        _remain  = max(0, int(_interval_secs - _elapsed))
        st.caption(f"⏱ Auto làm mới giá mỗi {_auto_interval_min} phút — "
                   f"lần tới sau **{_remain // 60}:{_remain % 60:02d}**")
        if _elapsed >= _interval_secs:
            with st.spinner("Auto làm mới giá..."):
                st.session_state.scan_cache = refresh_prices(source=source)
            st.session_state.scan_last_auto_refresh = _time.time()
            st.rerun()
        # Không sleep+rerun trong nhánh countdown — tránh chặn render tab_model/phaisinh/news


# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — DANH MỤC
# ═════════════════════════════════════════════════════════════════════════════
with tab_port:
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

        # Nút xóa mã — row riêng để tránh conflict với columns buttons trên
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

        # Dùng dữ liệu đã lưu (không phải bản đang edit) để tính P&L
        df_port = load_portfolio_manual()
        df_port = df_port[
            df_port["symbol"].astype(str).str.strip().ne("") &
            (df_port["quantity"] > 0)
        ]

    elif _port_mode == "📁 Upload CSV":
        uploaded = st.file_uploader("Upload file CSV danh mục", type="csv",
            help="Cột bắt buộc: symbol, quantity, avg_price. Tùy chọn: sector")
        if uploaded:
            import io
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

        # ── Nút làm mới tín hiệu real-time ───────────────────────────────────
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
            # Ưu tiên tín hiệu real-time; fallback sang cache Quick Scan
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

                # Signal: ưu tiên real-time, fallback cache
                _rt_sig = _port_sig_map.get(_sym)
                _cached = _cache_map.get(_sym, {})
                _sig = (_rt_sig.get("signal", "") if _rt_sig else None) or \
                       _cached.get("signal") or _cached.get("signal_class", "")
                _sigc = _SIG_COLOR.get(_sig, "#aaa")
                _sig_s = (f'<span style="color:{_sigc};font-weight:600">{_sig}</span>'
                          if _sig else "—")

                # % thay đổi phiên
                _cur_s = f"{_cur:,.0f}" if _cur and not pd.isna(_cur) else "—"
                _mv_s  = f"{_mv:,.0f}"  if _mv  and not pd.isna(_mv)  else "—"
                _pl_s  = (f'<span style="color:{"#00e676" if _pl>=0 else "#ff5252"}">'
                          f'{_pl:+,.0f}</span>') if _pl is not None and not pd.isna(_pl) else "—"
                _pp_s  = (f'<span style="color:{"#00e676" if _pp>=0 else "#ff5252"}">'
                          f'{_pp:+.2f}%</span>') if _pp is not None and not pd.isna(_pp) else "—"
                _chg_s = (f'<span style="color:{"#00e676" if _chg>=0 else "#ff5252"}">'
                          f'{_chg:+.2f}%</span>') if _chg is not None and not pd.isna(_chg) else "—"

                # Mô hình giá: gộp candle + chart patterns (rút gọn)
                _cpats = [p for p in [
                    _cached.get("candle_patterns",""), _cached.get("chart_patterns","")
                ] if p]
                _pat_s = " | ".join(_cpats) if _cpats else "—"
                # Rút gọn nếu quá dài
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


# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — MODEL AI
# ═════════════════════════════════════════════════════════════════════════════
with tab_model:
    st.header("🤖 Quản lý Model LSTM")

    # ── Helper: đọc trạng thái training đang chạy ──────────────────────────
    def _is_training() -> bool:
        return _TRAIN_LOG.exists()

    def _training_lines() -> list[str]:
        if not _TRAIN_LOG.exists():
            return []
        try:
            return _TRAIN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        except Exception:
            return []

    def _start_training(mode: str):
        _APP_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
        _TRAIN_LOG.write_text(f"[{time.strftime('%H:%M:%S')}] Bắt đầu training mode={mode}\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "vn_invest.train_lstm", "--mode", mode]
        with open(_TRAIN_LOG, "a", encoding="utf-8") as log_f:
            subprocess.Popen(cmd, stdout=log_f, stderr=log_f,
                             cwd=str(_APP_DIR), creationflags=subprocess.CREATE_NO_WINDOW
                             if sys.platform == "win32" else 0)

    def _stop_training():
        _TRAIN_LOG.unlink(missing_ok=True)

    # ── Trạng thái model ───────────────────────────────────────────────────
    info = get_model_info()
    col_info, col_action = st.columns([3, 2])

    with col_info:
        st.subheader("Trạng thái model")
        mi1, mi2, mi3 = st.columns(3)
        mi1.metric("Version đang dùng", info["version"].upper() if info["version"] != "none" else "Chưa có")
        mi2.metric("Số tickers Amibroker", info["tickers_available"])
        mi3.metric("Số features", info["n_features"])

        # Model files
        v7_exists = _V7_MODEL.exists()
        v6_exists = _V6_MODEL.exists()
        st.markdown(
            f"- **v7** ({_V7_MODEL.name}): {'✅ Có' if v7_exists else '❌ Chưa train'}"
            + (f" — `{time.strftime('%d/%m/%Y %H:%M', time.localtime(_V7_MODEL.stat().st_mtime))}`" if v7_exists else "")
        )
        st.markdown(
            f"- **v6** ({_V6_MODEL.name}): {'✅ Có' if v6_exists else '❌ Không có'}"
            + (f" — `{time.strftime('%d/%m/%Y %H:%M', time.localtime(_V6_MODEL.stat().st_mtime))}`" if v6_exists else "")
        )

        # Metrics từ lần train cuối
        if _METRICS_FILE.exists():
            try:
                m = json.loads(_METRICS_FILE.read_text(encoding="utf-8"))
                st.divider()
                st.subheader("Kết quả lần train gần nhất")
                ev = m.get("evaluation", {})
                cal = ev.get("calibrated", {})
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Mode", m.get("mode","—").upper())
                mc2.metric("Epochs chạy", m.get("epochs_run","—"))
                mc3.metric("Precision T+25", f"{ev.get('best_precision_pct',0):.1f}%")
                mc4.metric("Recall T+25",    f"{ev.get('best_recall_pct',0):.1f}%")

                st.markdown(f"""
**Ngưỡng tín hiệu (đã calibrate):**
| BUY-A | BUY-B | HOLD max | SELL-B |
|-------|-------|----------|--------|
| ≥ {cal.get('buy_a','—')} | ≥ {cal.get('buy_b','—')} | ≤ {cal.get('hold_max','—')} | ≤ {cal.get('sell_b','—')} |
""")
                trained_at = m.get("trained_at","")
                if trained_at:
                    st.caption(f"Train lúc: {trained_at[:16].replace('T',' ')}")

                # Loss curve
                if "loss" in m and "val_loss" in m:
                    st.subheader("Loss curve")
                    loss_df = pd.DataFrame({"Train loss": m["loss"], "Val loss": m["val_loss"]})
                    st.line_chart(loss_df, use_container_width=True)
            except Exception:
                pass

    with col_action:
        st.subheader("Huấn luyện lại")

        # Phát hiện model lạc hậu
        _outdated = False
        _outdated_reason = ""
        if v7_exists:
            model_age_days = (time.time() - _V7_MODEL.stat().st_mtime) / 86400
            newest_data = max(
                (f.stat().st_mtime for f in Path(info["history_dir"]).glob("*.csv")),
                default=0
            ) if Path(info["history_dir"]).exists() else 0
            data_newer = newest_data > _V7_MODEL.stat().st_mtime
            if model_age_days > 30:
                _outdated = True
                _outdated_reason = f"Model đã {int(model_age_days)} ngày chưa train lại"
            elif data_newer:
                _outdated = True
                _outdated_reason = "Có data Amibroker mới hơn model"

        if _outdated:
            st.warning(f"⚠️ {_outdated_reason}")
        elif v7_exists:
            st.success("✅ Model còn mới, chưa cần train lại")
        else:
            st.info("ℹ️ Chưa có model v7 — cần train lần đầu")

        st.divider()

        # Trạng thái đang training
        training_active = _is_training()

        if training_active:
            st.warning("⏳ **Đang training...**")
            log_lines = _training_lines()
            if log_lines:
                st.code("\n".join(log_lines), language=None)
            if st.button("🔄 Refresh trạng thái", use_container_width=True):
                st.rerun()
            if st.button("⛔ Hủy training", use_container_width=True, type="secondary"):
                _stop_training()
                st.rerun()
        else:
            # Nút train
            st.markdown("**Chọn chế độ training:**")

            if st.button("⚡ Train nhanh (dùng cache)", use_container_width=True, type="primary",
                         help="Dùng dataset cache đã build. Nhanh hơn 5x nếu cache còn đó."):
                _start_training("train")
                st.success("Đã bắt đầu training! Refresh để xem tiến độ.")
                time.sleep(1)
                st.rerun()

            if st.button("🔁 Rebuild cache + Train lại", use_container_width=True,
                         help="Đọc lại toàn bộ 440 tickers Amibroker, build dataset mới, rồi train. Mất ~20-30 phút."):
                # Xóa cache cũ để force rebuild
                cache_f = _APP_DIR / "data" / "dataset_cache.npz"
                cache_f.unlink(missing_ok=True)
                _start_training("train")
                st.success("Đang rebuild dataset và train lại...")
                time.sleep(1)
                st.rerun()

            if st.button("📊 Phân tích backtest", use_container_width=True,
                         help="Phân tích backtest_results.csv, không train model."):
                _start_training("analyze")
                st.success("Đang phân tích...")
                time.sleep(1)
                st.rerun()

        st.divider()
        st.subheader("Auto-retrain")
        _AUTORETRAIN_CFG = _APP_DIR / "data" / "autoretrain.json"
        _auto_cfg = {}
        if _AUTORETRAIN_CFG.exists():
            try:
                _auto_cfg = json.loads(_AUTORETRAIN_CFG.read_text(encoding="utf-8"))
            except Exception:
                pass

        auto_enabled   = st.checkbox("Tự động train khi model lạc hậu", value=_auto_cfg.get("enabled", False))
        auto_threshold = st.slider("Train lại sau N ngày", 7, 90, _auto_cfg.get("days_threshold", 30))

        if st.button("💾 Lưu cài đặt Auto-retrain", use_container_width=True):
            _AUTORETRAIN_CFG.write_text(
                json.dumps({"enabled": auto_enabled, "days_threshold": auto_threshold}, indent=2),
                encoding="utf-8"
            )
            st.success("Đã lưu!")

        # Kiểm tra và auto-trigger nếu bật
        if auto_enabled and not training_active and v7_exists:
            model_age = (time.time() - _V7_MODEL.stat().st_mtime) / 86400
            if model_age > auto_threshold:
                st.warning(f"⚡ Auto-retrain: model {int(model_age)} ngày — tự động bắt đầu train!")
                _start_training("train")
                time.sleep(1)
                st.rerun()
        elif auto_enabled and not training_active and not v7_exists:
            st.info("Auto-retrain bật: sẽ train lần đầu khi bạn mở tab này.")
            _start_training("train")
            time.sleep(1)
            st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — SECTION 2: CẢNH BÁO TELEGRAM
# ═════════════════════════════════════════════════════════════════════════════
with tab_model:
    st.divider()
    st.header("📢 Cảnh Báo Telegram")

    from vn_invest.alerter import (
        run_alert_scan, get_alert_history,
        _BUY_THRESHOLD, _SELL_THRESHOLD, _COOLDOWN_DAYS,
    )
    from vn_invest.screener import get_ami_scan_data as _get_ami_scan_data

    # Cấu hình alert
    with st.expander("⚙️ Cấu hình cảnh báo", expanded=False):
        al_c1, al_c2, al_c3 = st.columns(3)
        al_buy_thr  = al_c1.number_input("Ngưỡng BUY (composite ≥)", 50, 100, int(_BUY_THRESHOLD), step=5,
                                          help="Composite score >= ngưỡng này mới gửi cảnh báo mua")
        al_sell_thr = al_c2.number_input("Ngưỡng SELL (composite ≤)", 0, 50, int(_SELL_THRESHOLD), step=5,
                                          help="Composite score <= ngưỡng này mới gửi cảnh báo bán")
        al_cooldown = al_c3.number_input("Cooldown (ngày)", 1, 30, _COOLDOWN_DAYS,
                                          help="Không re-alert cùng mã+tín hiệu trong N ngày")
        al_use_lstm = st.checkbox("Dùng LSTM trong tính điểm tổng hợp", value=model_ready(),
                                   help="Tắt nếu không có model hoặc muốn chạy nhanh hơn")
        al_dry_run  = st.checkbox("Dry run (không gửi thật, chỉ xem kết quả)", value=False)

        # Ghi tạm vào env để alerter đọc
        os.environ["ALERT_BUY_THRESHOLD"]  = str(al_buy_thr)
        os.environ["ALERT_SELL_THRESHOLD"] = str(al_sell_thr)
        os.environ["ALERT_COOLDOWN_DAYS"]  = str(al_cooldown)

    # Trạng thái Telegram
    _tg_token   = os.getenv("TELEGRAM_TOKEN", "")
    _tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if _tg_token and _tg_chat_id:
        st.success(f"✅ Telegram đã cấu hình (chat_id: {_tg_chat_id})")
    else:
        st.warning("⚠️ Chưa cấu hình Telegram. Thêm TELEGRAM_TOKEN và TELEGRAM_CHAT_ID vào file .env")

    # Thông tin scan_result.csv
    _ami_rows_count = len(_get_ami_scan_data())
    st.caption(f"Nguồn: scan_result.csv — {_ami_rows_count} mã từ Amibroker Explorer")

    # Nút chạy
    btn_c1, btn_c2 = st.columns(2)
    with btn_c1:
        run_alert = st.button(
            "🚀 Quét & Gửi Cảnh Báo",
            use_container_width=True,
            type="primary",
            disabled=not _ami_rows_count,
            help="Quét toàn bộ, lọc tín hiệu chất lượng, gửi Telegram (có spam filter)"
        )
    with btn_c2:
        preview_alert = st.button(
            "👁️ Preview (không gửi)",
            use_container_width=True,
            disabled=not _ami_rows_count,
            help="Chạy dry-run để xem kết quả trước khi gửi thật"
        )

    if run_alert or preview_alert:
        _dry = al_dry_run or preview_alert
        _pb_al = st.progress(0)
        _st_al = st.empty()

        def _alert_progress(i, total, sym):
            _pb_al.progress(int((i + 1) / total * 100))
            _st_al.text(f"Đang xử lý {sym}... ({i+1}/{total})")

        with st.spinner("Đang quét và lọc tín hiệu..."):
            result = run_alert_scan(
                use_lstm=al_use_lstm,
                progress_callback=_alert_progress,
                dry_run=_dry,
            )

        _pb_al.empty(); _st_al.empty()

        # Thống kê kết quả
        r_c1, r_c2, r_c3, r_c4 = st.columns(4)
        r_c1.metric("Mã đã quét",          result["scanned"])
        r_c2.metric("Đạt ngưỡng chất lượng", result["qualified"])
        r_c3.metric("Đã gửi Telegram",     result["sent"] if not _dry else f"{result['sent']} (dry)")
        r_c4.metric("Bỏ qua (spam filter)", result["skipped_spam"])

        if not _dry and result["sent"] > 0:
            st.success(f"✅ Đã gửi {result['sent']} cảnh báo qua Telegram!")
        elif _dry:
            st.info("👁️ Dry run — không gửi thật. Bỏ tick 'Preview' để gửi.")

        # Bảng kết quả alert
        if result["alerts"]:
            st.subheader(f"Tín hiệu đạt ngưỡng ({len(result['alerts'])} mã)")
            _SIG_ICON = {"BUY-A":"🟢","BUY-B":"🟩","HOLD":"🟡","SELL-B":"🟠","SELL-A":"🔴"}
            df_alerts = pd.DataFrame(result["alerts"])
            df_alerts["Tín hiệu"] = df_alerts["signal"].map(lambda s: f"{_SIG_ICON.get(s,'')} {s}")
            display_alert_cols = {
                "symbol": "Mã", "close": "Giá", "pct_change": "% ngày",
                "comp_score": "Điểm TH", "ami_score": "AMI", "lstm_score": "LSTM",
                "tech_score": "KT", "Tín hiệu": "Tín hiệu", "risk": "Rủi ro", "phase": "Giai đoạn",
            }
            df_disp = df_alerts[[c for c in display_alert_cols if c in df_alerts.columns]].rename(columns=display_alert_cols)
            st.dataframe(df_disp, use_container_width=True, hide_index=True,
                column_config={
                    "Giá":     st.column_config.NumberColumn(format="%,.0f"),
                    "% ngày":  st.column_config.NumberColumn(format="%.2f%%"),
                    "Điểm TH": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                    "AMI":     st.column_config.NumberColumn(format="%.0f"),
                    "LSTM":    st.column_config.NumberColumn(format="%.1f"),
                    "KT":      st.column_config.NumberColumn(format="%.0f"),
                })
        else:
            st.info("Không có mã nào đạt ngưỡng chất lượng trong lần quét này.")

    st.divider()

    # Lịch sử đã gửi
    with st.expander("📋 Lịch sử cảnh báo đã gửi", expanded=False):

        hist_data = get_alert_history()
        if hist_data:
            df_hist = pd.DataFrame(hist_data)
            df_hist["sent_at"] = pd.to_datetime(df_hist["sent_at"]).dt.strftime("%d/%m/%Y %H:%M")
            st.dataframe(df_hist.rename(columns={
                "symbol": "Mã", "signal": "Tín hiệu", "score": "Điểm TH", "sent_at": "Thời gian gửi"
            }), use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có lịch sử cảnh báo nào.")

# ═════════════════════════════════════════════════════════════════════════════
# TAB 6 — PHÁI SINH
# ═════════════════════════════════════════════════════════════════════════════
with tab_phaisinh:
    from vn_invest.phaisinh_tab import render_phaisinh_tab
    render_phaisinh_tab()


# ═════════════════════════════════════════════════════════════════════════════
# TAB 7 — TIN TỨC THỊ TRƯỜNG
# ═════════════════════════════════════════════════════════════════════════════
with tab_news:
    st.write("TEST TAB NEWS OK")
    from vn_invest.news_fetcher import _fetch_all_rss, _RSS_SOURCES

    st.header("📰 Tin Tức Thị Trường")
    st.caption(f"{len(_RSS_SOURCES)} nguồn: VnEconomy · VnExpress · CafeF · Investing.com VN · NguoiDuaTin · Reuters · SCMP · Mining.com · SteelOrbis")

    # Bộ lọc
    _nc1, _nc2, _nc3 = st.columns([2, 2, 1])
    _news_lang = _nc1.selectbox("Ngôn ngữ", ["Tất cả", "Tiếng Việt", "English"], key="news_lang")
    _news_src  = _nc2.selectbox(
        "Nguồn",
        ["Tất cả"] + sorted({s for s, _, _ in _RSS_SOURCES}),
        key="news_src",
    )
    _news_kw   = _nc3.text_input("Tìm kiếm", placeholder="vnindex, thep...", key="news_kw")

    _btn_reload = st.button("🔄 Tải lại tin tức", key="btn_news_reload")
    if _btn_reload:
        # Xóa cache để fetch lại
        import vn_invest.news_fetcher as _nf
        _nf._cache.clear()

    try:
        with st.spinner("Đang tải tin tức..."):
            _all_news = _fetch_all_rss()
        st.write(f"DEBUG fetch OK: {len(_all_news)} bài")
    except Exception as _e:
        st.error(f"DEBUG fetch lỗi: {_e}")
        _all_news = []

    # Lọc
    _filtered = list(_all_news)
    if _news_lang == "Tiếng Việt":
        _filtered = [a for a in _filtered if a.get("lang") == "vi"]
    elif _news_lang == "English":
        _filtered = [a for a in _filtered if a.get("lang") == "en"]
    if _news_src != "Tất cả":
        _filtered = [a for a in _filtered if a.get("source") == _news_src]
    if _news_kw.strip():
        _kw = _news_kw.strip().lower()
        _filtered = [
            a for a in _filtered
            if _kw in a.get("title", "").lower() or _kw in a.get("desc", "").lower()
        ]

    st.write(f"DEBUG filtered: {len(_filtered)} bài")

    # Sắp xếp mới nhất trước
    _filtered.sort(key=lambda x: x.get("date", ""), reverse=True)

    st.markdown(f"**{len(_filtered)} bài** · Cache 10 phút")
    st.divider()

    # Nhóm theo nguồn
    _by_source: dict = {}
    for _art in _filtered:
        _by_source.setdefault(_art["source"], []).append(_art)

    if not _filtered:
        st.info("Không có bài viết nào phù hợp với bộ lọc.")
    else:
        for _src_name, _arts in _by_source.items():
            _lang_flag = "🇻🇳" if _arts[0].get("lang") == "vi" else "🌐"
            with st.expander(f"{_lang_flag} **{_src_name}** — {len(_arts)} bài", expanded=True):
                for _a in _arts[:20]:
                    _col_a, _col_b = st.columns([5, 1])
                    with _col_a:
                        if _a.get("url"):
                            st.markdown(f"**[{_a['title']}]({_a['url']})**")
                        else:
                            st.markdown(f"**{_a['title']}**")
                        if _a.get("desc"):
                            st.caption(_a["desc"][:200])
                    with _col_b:
                        st.caption(_a.get("date", "")[:10])
