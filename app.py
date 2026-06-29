"""Dashboard phân tích chứng khoán Việt Nam — Streamlit entry point (refactored)."""
import os
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

import streamlit as st

from vn_invest.data import (
    get_price_history, get_financial_ratios_history, get_company_overview,
    get_company_news, get_company_events, get_company_dividends,
    get_company_shareholders, get_financial_statements, get_stock_status,
    get_side_stats, get_market_indices, get_capital_history, get_macro_data,
    get_market_breadth,
)
from vn_invest.investing import COMMON_PAIRS, get_global_price
from vn_invest.indicators import add_all_indicators
from vn_invest.screener import load_cache

# ── Cache functions ───────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_overview(sym, src):
    vci = get_company_overview(sym, source="VCI")
    kbs = get_company_overview(sym, source=src) if src != "VCI" else {}
    merged = {**kbs, **{k: v for k, v in vci.items() if v}}
    merged.setdefault("company_name",  merged.get("organ_name", ""))
    merged.setdefault("short_name",    merged.get("organ_short_name", ""))
    merged.setdefault("industry_name", merged.get("sector", ""))
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
    try:
        if sym in COMMON_PAIRS or "/" in sym:
            from datetime import datetime, timedelta
            start = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d")
            end   = datetime.now().strftime("%Y-%m-%d")
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
    end   = datetime.now().strftime("%Y-%m-%d")
    for macro_sym in ["SP500","NASDAQ","DOW","GOLD","BRENT","WTI","BTC/USD","EUR/USD","USD/VND","DXY"]:
        try:
            df = get_global_price(macro_sym, start=start, end=end)
            if df is not None and not df.empty:
                cur = df.iloc[-1]["close"]
                pct = (cur - df.iloc[-2]["close"]) / df.iloc[-2]["close"] * 100 if len(df) > 1 else None
                results[macro_sym] = (cur, pct)
        except Exception:
            pass
    return results

_ASIA_INDICES = {
    "Nikkei":    "^N225",
    "Hang Seng": "^HSI",
    "Shanghai":  "000001.SS",
    "KOSPI":     "^KS11",
    "ASX200":    "^AXJO",
    "STI":       "^STI",
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
        return tuple(dict.fromkeys(all_syms))
    except Exception:
        return ()

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_market_breadth(symbols_key: str, symbols: tuple):
    return get_market_breadth(list(symbols))

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_capital_history(sym):
    return get_capital_history(sym, source="VCI")

@st.cache_data(ttl=21600, show_spinner=False)
def _fetch_macro():
    return get_macro_data()

# ── Paths ─────────────────────────────────────────────────────────────────────
_APP_DIR      = Path(__file__).parent
_METRICS_FILE = _APP_DIR / "data" / "model_metrics.json"
_TRAIN_LOG    = _APP_DIR / "data" / "train_running.log"
_V7_MODEL     = Path(r"C:\AmibrokerData\stock_lstm_v7.keras")
_V6_MODEL     = Path(r"C:\AmibrokerData\stock_lstm_v6_multi.keras")

# ── Page config ───────────────────────────────────────────────────────────────
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

    _indices = _fetch_market_indices()
    _IDX_LABEL = {
        "VNINDEX": "VN-Index", "HNXINDEX": "HNX",
        "UPCOMINDEX": "UPCOM", "VN30": "VN30", "HNX30": "HNX30",
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
            _val_str   = f"{_val:,.2f}" if _val else "—"
            _delta_str = f"{_pct:+.2f}%" if _pct is not None else (f"{_chg:+.2f}" if _chg else None)
            _cols[_ci].metric(_lbl, _val_str, _delta_str, delta_color="normal")
        st.divider()

    _all_listed = _fetch_all_listed_symbols()
    if not _all_listed:
        _cache_syms_fb = st.session_state.get("scan_cache") or load_cache()
        _all_listed = tuple(r["symbol"] for r in _cache_syms_fb if r.get("symbol")) if _cache_syms_fb else ()
    _breadth: dict = {}
    if _all_listed:
        _breadth = _fetch_market_breadth(f"breadth_{len(_all_listed)}", _all_listed)
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

        st.markdown('<div style="font-size:12px;font-weight:700;color:#ddd;margin-bottom:6px">Độ rộng thị trường</div>',
                    unsafe_allow_html=True)
        _breadth_row("HOSE", "HOSE")
        _breadth_row("HNX", "HNX")
        _breadth_row("UPCOM", "UPCOM")

        _sig_counts: dict = {}
        for _r in (_cache_syms or []):
            _s = _r.get("signal") or _r.get("signal_class") or "HOLD"
            _sig_counts[_s] = _sig_counts.get(_s, 0) + 1
        _tot_sig = sum(_sig_counts.values()) or 1
        _SIG_COLOR = {"BUY-A":"#00c853","BUY-B":"#69f0ae","HOLD":"#888","SELL-B":"#ff8a65","SELL-A":"#ff1744"}
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

    macro_data = _fetch_macro_ticker_data()
    asia_data  = _fetch_asia_indices()
    _vnidx = _idx_map.get("VNINDEX")
    vnindex_entry = {}
    if _vnidx:
        _v = _vnidx.get("index_value")
        _p = _vnidx.get("pct_change")
        if _v is not None:
            vnindex_entry = {"VN-Index": (_v, _p)}
    combined = {**vnindex_entry, **asia_data, **macro_data}

    if combined:
        def _ticker_item(sym, price, pct):
            val_str = f"{price:,.2f}" if price is not None else "—"
            if pct is not None:
                color   = "#00c853" if pct >= 0 else "#ff1744"
                arrow   = "▲" if pct >= 0 else "▼"
                pct_str = f'<span style="color:{color}">{arrow}{abs(pct):.2f}%</span>'
            else:
                pct_str = '<span style="color:#888">—</span>'
            return (
                f'<span style="color:#aaa;font-weight:600">{sym}</span>'
                f'&nbsp;<span style="color:#fff">{val_str}</span>'
                f'&nbsp;{pct_str}'
                f'&nbsp;&nbsp;<span style="color:#333">|</span>&nbsp;&nbsp;'
            )

        items_html     = "".join(_ticker_item(sym, price, pct) for sym, (price, pct) in combined.items())
        ticker_content = items_html * 2
        speed          = max(30, len(combined) * 3)

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
    source       = st.selectbox("Nguồn dữ liệu", ["KBS", "VCI"], index=0)
    days         = st.slider("Lịch sử (ngày)", 60, 365, 120)

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

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_basic, tab_tech, tab_scan, tab_fund, tab_port, tab_model, tab_phaisinh, tab_news = st.tabs([
    "📊 Cơ Bản", "📉 Kỹ Thuật", "🔍 Quick Scan", "🏦 Lọc Cơ Bản",
    "💼 Danh Mục", "🤖 Model AI", "⚡ Phái Sinh", "📰 Tin Tức",
])

# ── ctx dict (shared context) ─────────────────────────────────────────────────
ctx = {
    "symbol_input":        symbol_input,
    "source":              source,
    "days":                days,
    "fetch_overview":      _fetch_overview,
    "fetch_ratio_hist":    _fetch_ratio_hist,
    "fetch_stock_status":  _fetch_stock_status,
    "fetch_statements":    _fetch_statements,
    "fetch_shareholders":  _fetch_shareholders,
    "fetch_news":          _fetch_news,
    "fetch_events":        _fetch_events,
    "fetch_dividends":     _fetch_dividends,
    "fetch_price":         _fetch_price,
    "fetch_side_stats":    _fetch_side_stats,
    "fetch_market_breadth": _fetch_market_breadth,
    "fetch_capital_history": _fetch_capital_history,
    "fetch_macro":         _fetch_macro,
    "app_dir":             _APP_DIR,
    "metrics_file":        _METRICS_FILE,
    "train_log":           _TRAIN_LOG,
    "v7_model":            _V7_MODEL,
    "v6_model":            _V6_MODEL,
}

# ── Tab routing ───────────────────────────────────────────────────────────────
from tabs import tab_basic as _tb, tab_tech as _tt, tab_scan as _ts
from tabs import tab_port as _tp, tab_model as _tm, tab_phaisinh as _tps, tab_news as _tn
from tabs import tab_fundamental as _tf

with tab_basic:
    _tb.render(ctx)

with tab_tech:
    _tt.render(ctx)

with tab_scan:
    _ts.render(ctx)

with tab_fund:
    _tf.render(ctx)

with tab_port:
    _tp.render(ctx)

with tab_model:
    _tm.render(ctx)

with tab_phaisinh:
    _tps.render(ctx)

with tab_news:
    _tn.render(ctx)
