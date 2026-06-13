"""Fetch dữ liệu từ vnstock 4.x (giá lịch sử, chỉ số tài chính).

API mới: Vnstock().stock(symbol, source) thay vì Stock(symbol, source)
finance.ratio trả long-format: cột item_id, các cột còn lại là kỳ (2025-Q3...)
price_board: cột 'symbol' (không phải 'ticker'), 'close_price'
"""
import math
import warnings
from datetime import datetime, timedelta
import pandas as pd

from .config import DEFAULT_SOURCE, DEFAULT_DAYS

warnings.filterwarnings("ignore", category=DeprecationWarning)


def _clean_float(val):
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _get_stock(symbol: str, source: str):
    """Tạo StockComponents object (vnstock 4.x). Suppress banner prints."""
    import io, sys
    from vnstock import Vnstock
    _old_stdout, _old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = io.StringIO()
    try:
        obj = Vnstock().stock(symbol=symbol, source=source)
    finally:
        sys.stdout, sys.stderr = _old_stdout, _old_stderr
    return obj


def get_price_history(symbol: str, days: int = DEFAULT_DAYS, source: str = DEFAULT_SOURCE) -> pd.DataFrame:
    """Lấy lịch sử giá OHLCV. Trả DataFrame: time, open, high, low, close, volume."""
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    stock = _get_stock(symbol, source)
    df = stock.quote.history(start=start, end=end, interval="1D")
    df = df.sort_values("time").reset_index(drop=True)
    # Strip giờ khỏi timestamp (vnstock trả 07:00:00 UTC+7)
    df["time"] = df["time"].dt.normalize()
    return df


def get_financial_ratios(symbol: str, source: str = DEFAULT_SOURCE) -> list[dict]:
    """Lấy chỉ số tài chính kỳ gần nhất. Trả list of {item_id, value, period}."""
    try:
        stock = _get_stock(symbol, source)
        df = stock.finance.ratio(period="annual")
        if df is None or df.empty:
            return []
        meta_cols = {"item", "item_en", "item_id"}
        period_cols = [c for c in df.columns if c not in meta_cols]
        if not period_cols:
            return []
        latest_col = period_cols[0]
        rows = []
        for _, row in df.iterrows():
            item_id = row.get("item_id")
            if not item_id:
                continue
            val = _clean_float(row.get(latest_col))
            rows.append({"item_id": str(item_id), "value": val, "period": str(latest_col)})
        return rows
    except Exception:
        return []


def get_financial_ratios_history(
    symbol: str,
    period: str = "annual",
    source: str = DEFAULT_SOURCE,
    n_periods: int = 8,
) -> dict:
    """Lấy lịch sử chỉ số tài chính theo nhiều kỳ.

    KBS chỉ hỗ trợ annual. Khi period="quarter" tự động thử VCI trước,
    fallback KBS/annual nếu VCI thất bại.

    Returns:
        {
          "periods": ["2024", "2023", ...],
          "data": {"p_e": [15.2, 12.1, ...], ...},
          "actual_period": "annual"|"quarter",
          "actual_source": "KBS"|"VCI",
        }
    """
    # Xác định source phù hợp
    # KBS không hỗ trợ quarterly → dùng VCI khi cần quý
    import re

    def _is_quarterly_label(label: str) -> bool:
        """True nếu nhãn kỳ có dạng quarterly: 2025-Q3, 2025-Q4_1, v.v."""
        return bool(re.search(r'-Q\d', str(label)))

    def _year_of(label: str) -> str:
        """Trích xuất năm từ nhãn kỳ bất kỳ."""
        m = re.match(r'(\d{4})', str(label))
        return m.group(1) if m else str(label)

    def _is_yearend(label: str) -> bool:
        """True nếu là kỳ cuối năm: Q4 hoặc nhãn dạng YYYY (không có -Q)."""
        s = str(label)
        if not _is_quarterly_label(s):
            return True          # nhãn dạng "2024" → đúng là năm
        return bool(re.search(r'-Q4', s))   # Q4 = đại diện cả năm

    def _is_future(label: str) -> bool:
        """True nếu kỳ này chưa kết thúc (tương lai). Tránh hiện data chế."""
        from datetime import date
        today = date.today()
        s = str(label)
        m_q = re.match(r'(\d{4})-Q(\d)', s)
        if m_q:
            yr, q = int(m_q.group(1)), int(m_q.group(2))
            end_month = q * 3
            # Kỳ Qn kết thúc ngày cuối tháng end_month — nếu tháng đó chưa qua → tương lai
            end_year = yr
            if today.year < end_year:
                return True
            if today.year == end_year and today.month <= end_month:
                return True
            return False
        m_yr = re.match(r'^(\d{4})$', s)
        if m_yr:
            yr = int(m_yr.group(1))
            return yr >= today.year   # năm chưa kết thúc
        return False

    # Chỉ thử một lần: lấy về raw data rồi xử lý phía sau
    # Chỉ KBS trả dữ liệu dạng quarterly (~4 kỳ gần nhất) dù gọi period gì
    # VCI/annual = rác, VCI/quarter = chỉ có 2018
    # → Luôn dùng KBS, xử lý nhãn phía sau
    try:
        stock = _get_stock(symbol, source)
        df = stock.finance.ratio(period="annual")   # KBS trả quarterly dù gọi annual
        if df is None or df.empty:
            return {"periods": [], "data": {}, "actual_period": period, "actual_source": source}

        meta_cols = {"item", "item_en", "item_id"}
        all_period_cols = [c for c in df.columns if c not in meta_cols]
        if not all_period_cols:
            return {"periods": [], "data": {}, "actual_period": period, "actual_source": source}

        # Loại bỏ kỳ tương lai trước mọi xử lý
        all_period_cols = [c for c in all_period_cols if not _is_future(c)]

        if period == "annual":
            # Lọc chỉ Q4 (đại diện năm), dedup theo năm, bỏ Q4_1 (duplicate)
            seen_years: set[str] = set()
            period_cols = []
            for c in all_period_cols:
                s = str(c)
                if not _is_quarterly_label(s):
                    period_cols.append(c)          # nhãn YYYY → giữ nguyên
                elif re.search(r'-Q4$', s):        # Q4 chính xác (không phải Q4_1)
                    yr = _year_of(s)
                    if yr not in seen_years:
                        seen_years.add(yr)
                        period_cols.append(c)
            # Nếu không có Q4 nào (dữ liệu quá mới/cũ), fallback dùng tất cả
            if not period_cols:
                period_cols = all_period_cols
        else:
            # Quý: lấy tất cả, bỏ Q4_1 (duplicate của Q4)
            period_cols = [c for c in all_period_cols if not re.search(r'-Q\d_\d', str(c))]
            if not period_cols:
                period_cols = all_period_cols

        period_cols = period_cols[:n_periods]

        def _display_label(c) -> str:
            s = str(c)
            if period == "annual" and _is_quarterly_label(s):
                return _year_of(s)   # "2025-Q4" → "2025"
            return s

        data: dict[str, list] = {}
        for _, row in df.iterrows():
            item_id = row.get("item_id")
            if not item_id:
                continue
            vals = [_clean_float(row.get(c)) for c in period_cols]
            data[str(item_id)] = vals

        return {
            "periods":       [_display_label(c) for c in period_cols],
            "data":          data,
            "actual_period": period,
            "actual_source": source,
        }
    except Exception:
        return {"periods": [], "data": {}, "actual_period": period, "actual_source": source}


def get_price_board(symbols: list[str], source: str = DEFAULT_SOURCE) -> pd.DataFrame:
    """Lấy giá hiện tại cho nhiều mã (batch). Cột: symbol, close_price."""
    from vnstock import Trading
    t = Trading(source=source, symbol=symbols[0])
    return t.price_board(symbols_list=symbols)


def get_company_overview(symbol: str, source: str = DEFAULT_SOURCE) -> dict:
    """Lấy thông tin tổng quan công ty."""
    try:
        stock = _get_stock(symbol, source)
        info = stock.company.overview()
        if hasattr(info, "iloc") and not info.empty:
            rec = info.iloc[0].to_dict()
        elif isinstance(info, dict):
            rec = info
        else:
            return {}
        return {k: (None if (isinstance(v, float) and math.isnan(v)) else v) for k, v in rec.items()}
    except Exception:
        return {}


def get_company_news(symbol: str, source: str = "VCI") -> list[dict]:
    """Lấy tin tức công ty. Trả list of {title, date, source_name, url, summary, content}."""
    try:
        stock = _get_stock(symbol, source)
        df = stock.company.news()
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            def _get(*keys):
                for k in keys:
                    v = row.get(k)
                    if v is not None and str(v).strip() not in ("None", "", "nan"):
                        return str(v).strip()
                return None

            title   = _get("news_title", "title", "Title")
            if not title:
                continue
            date    = _get("public_date", "published_date", "date")
            src     = _get("news_source", "source")
            url     = _get("news_source_link", "url", "link", "news_url")
            summary = _get("news_short_content", "news_sub_title", "summary")
            content = _get("news_full_content", "content")

            # Rút gọn date về YYYY-MM-DD
            if date and "T" in date:
                date = date[:10]

            results.append({
                "title":   title,
                "date":    date or "",
                "source":  src or "",
                "url":     url or "",
                "summary": summary or "",
                "content": content or "",
            })
        return results[:20]
    except Exception:
        return []


def get_stock_status(symbol: str, source: str = "VCI") -> dict:
    """Phân tích trạng thái giao dịch: niêm yết, cảnh báo, tạm ngừng, kiểm soát.

    Returns:
        {
          "status":   "normal"|"warning"|"restricted"|"suspended"|"delisted",
          "badges":   [{"label":..., "level":"danger"|"warning"|"info"}],
          "alerts":   [str],   # Danh sách cảnh báo cụ thể
          "events":   [dict],  # Sự kiện liên quan từ vnstock
          "stats":    dict,    # trading_stats raw (giá, volume, range...)
        }
    """
    result = {"status": "normal", "badges": [], "alerts": [], "events": [], "stats": {}}
    try:
        stock = _get_stock(symbol, source)

        # ── 1. trading_stats ────────────────────────────────────────────────
        try:
            ts = stock.company.trading_stats()
            if ts is not None and not ts.empty:
                row = ts.iloc[0]
                stats = {
                    "listing":          bool(row.get("listing", True)),
                    "com_group":        str(row.get("com_group_code", "")),
                    "current_price":    _clean_float(row.get("current_price")),
                    "market_cap":       _clean_float(row.get("market_cap")),
                    "avg_vol_1m":       _clean_float(row.get("average_match_volume1_month")),
                    "high_1y":          _clean_float(row.get("highest_price1_year")),
                    "low_1y":           _clean_float(row.get("lowest_price1_year")),
                    "foreign_pct":      _clean_float(row.get("foreigner_percentage")),
                    "state_pct":        _clean_float(row.get("state_percentage")),
                    "free_float_pct":   _clean_float(row.get("free_float_percentage")),
                    "in_cu":            bool(row.get("in_cu", False)),
                    "rating":           str(row.get("rating") or ""),
                    "target_price":     _clean_float(row.get("target_price")),
                    "upside_pct":       _clean_float(row.get("upside_to_target_percent")),
                    "analyst":          str(row.get("analyst") or ""),
                }
                result["stats"] = stats

                # Phân tích trạng thái từ trading_stats
                if not stats["listing"] or stats["com_group"] == "OTC":
                    result["status"] = "delisted"
                    result["badges"].append({"label": "🚫 Đã hủy niêm yết / OTC", "level": "danger"})
                    result["alerts"].append("Cổ phiếu không còn giao dịch trên sàn chính thức (HOSE/HNX/UPCOM).")

                if stats["avg_vol_1m"] is not None and stats["avg_vol_1m"] == 0:
                    if result["status"] == "normal":
                        result["status"] = "suspended"
                    result["badges"].append({"label": "⏸ Tạm ngừng giao dịch", "level": "danger"})
                    result["alerts"].append("Khối lượng khớp lệnh trung bình 1 tháng = 0 → khả năng đang bị tạm ngừng giao dịch.")

                price = stats["current_price"]
                if price and price < 5000 and stats["listing"]:
                    if result["status"] == "normal":
                        result["status"] = "warning"
                    result["badges"].append({"label": "⚠️ Giá dưới 5.000đ", "level": "warning"})
                    result["alerts"].append(f"Giá hiện tại {price:,.0f}đ < 5.000đ — ngưỡng cảnh báo hủy niêm yết theo quy định HOSE.")

                if stats["com_group"] in ("", "OTC") and stats["listing"]:
                    result["badges"].append({"label": "⚠️ Ngoài rổ chỉ số", "level": "warning"})

        except Exception:
            pass

        # ── 2. Events — lọc cảnh báo, hạn chế, kiểm soát ───────────────────
        _WARN_CODES = {"SUSP", "WARN", "CTRL", "DELIST", "RESTRICT"}
        _WARN_KEYWORDS = [
            "cảnh báo", "hạn chế giao dịch", "kiểm soát", "tạm ngừng",
            "tạm dừng niêm yết", "hủy niêm yết", "đặc biệt",
            "suspension", "warning", "delisting", "restricted",
        ]
        try:
            from datetime import date as _date, timedelta
            _today = _date.today()
            _6M_AGO = _today - timedelta(days=180)

            ev_df = stock.company.events()
            if ev_df is not None and not ev_df.empty:
                for _, ev in ev_df.iterrows():
                    code  = str(ev.get("event_code", "")).upper()
                    title = str(ev.get("event_title_vi", "") or ev.get("event_name_vi", ""))
                    date  = str(ev.get("display_date1", "") or ev.get("public_date", ""))[:10]

                    # Chỉ xét sự kiện trong 6 tháng gần nhất
                    try:
                        from datetime import datetime
                        ev_date = datetime.strptime(date, "%Y-%m-%d").date()
                        if ev_date < _6M_AGO:
                            continue   # sự kiện cũ, bỏ qua
                    except Exception:
                        pass  # không parse được ngày → vẫn xét

                    is_warn = (code in _WARN_CODES or
                               any(kw in title.lower() for kw in _WARN_KEYWORDS))
                    if is_warn:
                        result["events"].append({
                            "code":  code,
                            "title": title,
                            "date":  date,
                        })
                        if result["status"] == "normal":
                            result["status"] = "warning"

                        if code == "SUSP" or "tạm ngừng" in title.lower() or "tạm dừng" in title.lower():
                            result["status"] = "suspended"
                            result["badges"].append({"label": "⏸ Sự kiện tạm ngừng giao dịch", "level": "danger"})
                        elif code in ("WARN", "CTRL") or "cảnh báo" in title.lower() or "kiểm soát" in title.lower():
                            if result["status"] not in ("delisted", "suspended"):
                                result["status"] = "restricted"
                            result["badges"].append({"label": "⚠️ Cổ phiếu diện cảnh báo/kiểm soát", "level": "warning"})
                        elif "hủy" in title.lower() or "delisting" in title.lower():
                            result["status"] = "delisted"
                            result["badges"].append({"label": "🚫 Sự kiện hủy niêm yết", "level": "danger"})
        except Exception:
            pass

        # Nếu không có badge nào → bình thường
        if not result["badges"]:
            result["badges"].append({"label": "✅ Giao dịch bình thường", "level": "info"})

        # Deduplicate badges
        seen_labels = set()
        result["badges"] = [b for b in result["badges"]
                            if not (b["label"] in seen_labels or seen_labels.add(b["label"]))]

    except Exception:
        pass
    return result


def get_financial_statements(symbol: str, period: str = "quarterly", source: str = "VCI") -> dict:
    """Lấy BCTC: KQKD, CĐKT, LCTT. period='quarterly'|'annual'.

    Returns:
        {
          "periods": ["2026-Q1", "2025-Q4", ...],
          "income":  {item_id: {"label": ..., "values": [...]}},
          "balance": {item_id: {"label": ..., "values": [...]}},
          "cashflow":{item_id: {"label": ..., "values": [...]}},
        }
    """
    import re
    result = {"periods": [], "income": {}, "balance": {}, "cashflow": {}}
    try:
        stock = _get_stock(symbol, source)
        meta_cols = {"item", "item_en", "item_id"}

        def _parse_statement(fn_name: str) -> tuple[list[str], dict]:
            """Đọc 1 báo cáo, trả (periods, {item_id: {label, values}})."""
            try:
                df = getattr(stock.finance, fn_name)(period=period, lang="vi")
            except Exception:
                return [], {}
            if df is None or df.empty:
                return [], {}

            # Cột period thực tế: tất cả cột không phải meta
            # Có thể trùng tên (nhiều '2025') → lấy qua iloc
            all_cols = list(df.columns)
            period_idx = [i for i, c in enumerate(all_cols) if c not in meta_cols]
            raw_labels = [all_cols[i] for i in period_idx]

            # Đặt lại tên kỳ: nếu trùng tên thêm hậu tố thứ tự
            seen: dict[str, int] = {}
            periods_clean = []
            for lbl in raw_labels:
                s = str(lbl)
                seen[s] = seen.get(s, 0) + 1
                if seen[s] == 1:
                    periods_clean.append(s)
                else:
                    # Đây là kỳ sau cùng năm → gắn Q1/Q2...
                    periods_clean.append(f"{s}-#{seen[s]}")

            # Lọc tương lai (cùng logic data.py)
            from datetime import date
            today = date.today()
            def _future(lbl):
                m = re.match(r'^(\d{4})$', lbl)
                if m: return int(m.group(1)) >= today.year
                return False
            valid_idx = [i for i, l in enumerate(periods_clean) if not _future(l)]
            periods_final = [periods_clean[i] for i in valid_idx]
            col_positions  = [period_idx[i] for i in valid_idx]

            data: dict[str, dict] = {}
            for _, row in df.iterrows():
                item_id = str(row.get("item_id", "")).strip()
                label   = str(row.get("item", "")).strip()
                if not item_id:
                    continue
                vals = []
                row_list = list(row)
                for pos in col_positions:
                    try:
                        vals.append(_clean_float(row_list[pos]))
                    except Exception:
                        vals.append(None)
                data[item_id] = {"label": label, "values": vals}

            return periods_final, data

        periods_is, income   = _parse_statement("income_statement")
        periods_bs, balance  = _parse_statement("balance_sheet")
        periods_cf, cashflow = _parse_statement("cash_flow")

        # Dùng periods dài nhất (thường giống nhau)
        result["periods"]  = periods_is or periods_bs or periods_cf
        result["income"]   = income
        result["balance"]  = balance
        result["cashflow"] = cashflow

    except Exception:
        pass
    return result


def get_company_shareholders(symbol: str, source: str = "VCI") -> dict:
    """Lấy cơ cấu cổ đông: cổ đông lớn, ban lãnh đạo, công ty con, tỷ lệ tổng hợp."""
    result = {"shareholders": [], "officers": [], "subsidiaries": [], "summary": {}}
    try:
        stock = _get_stock(symbol, source)

        # Cổ đông lớn
        try:
            df = stock.company.shareholders()
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    pct = _clean_float(r.get("share_own_percent"))
                    result["shareholders"].append({
                        "name":    str(r.get("share_holder", "—")),
                        "percent": round(pct * 100, 2) if pct else None,
                        "quantity": r.get("quantity"),
                        "updated": str(r.get("update_date", ""))[:10],
                    })
        except Exception:
            pass

        # Ban lãnh đạo
        try:
            df = stock.company.officers()
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    pct = _clean_float(r.get("officer_own_percent"))
                    result["officers"].append({
                        "name":     str(r.get("officer_name", "—")),
                        "position": str(r.get("officer_position", "—")),
                        "percent":  round(pct * 100, 2) if pct else None,
                        "quantity": r.get("officer_own_quantity"),
                    })
        except Exception:
            pass

        # Công ty con / liên kết
        try:
            df = stock.company.subsidiaries()
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    pct = _clean_float(r.get("ownership_percent"))
                    result["subsidiaries"].append({
                        "name":    str(r.get("organ_name", "—")),
                        "code":    str(r.get("sub_organ_code", "")),
                        "percent": round(pct * 100, 2) if pct else None,
                    })
        except Exception:
            pass

        # Tỷ lệ tổng hợp từ overview
        try:
            ov = stock.company.overview()
            if ov is not None and not (hasattr(ov, "empty") and ov.empty):
                row = ov.iloc[0] if hasattr(ov, "iloc") else ov
                def _pct(k): v = _clean_float(row.get(k)); return round(v * 100, 2) if v else None
                result["summary"] = {
                    "foreign_pct":     _pct("foreigner_percentage"),
                    "foreign_max_pct": _pct("maximum_foreign_percentage"),
                    "state_pct":       _pct("state_percentage"),
                    "free_float_pct":  _pct("free_float_percentage"),
                    "market_cap":      _clean_float(row.get("market_cap")),
                    "issue_share":     _clean_float(row.get("issue_share")),
                }
        except Exception:
            pass

    except Exception:
        pass
    return result


def get_company_events(symbol: str, source: str = "VCI") -> list[dict]:
    """Lấy sự kiện công ty (ĐHCĐ, chia cổ tức, phát hành...). Trả list of dict."""
    try:
        stock = _get_stock(symbol, source)
        df = stock.company.events()
        if df is None or df.empty:
            return []
        cols = list(df.columns)
        results = []
        for _, row in df.iterrows():
            item = {}
            for alias, keys in [
                ("title",    ["event_name", "title", "name", "event_title", "EventName"]),
                ("date",     ["event_date", "date", "ex_date", "record_date", "Date"]),
                ("type",     ["event_type", "type", "category"]),
                ("value",    ["value", "ratio", "dividend_amount", "details"]),
            ]:
                for k in keys:
                    if k in cols and row.get(k) is not None:
                        item[alias] = str(row[k])
                        break
            if item.get("title") or item.get("date"):
                results.append(item)
        return results[:15]
    except Exception:
        return []


def get_company_dividends(symbol: str, source: str = "VCI") -> list[dict]:
    """Lấy lịch sử cổ tức. Trả list of {exercise_date, cash_dividend_rate, ...}."""
    try:
        stock = _get_stock(symbol, source)
        df = stock.company.dividends()
        if df is None or df.empty:
            return []
        return df.head(10).to_dict("records")
    except Exception:
        return []


def get_macro_data() -> dict:
    """Lấy dữ liệu vĩ mô Việt Nam từ IMF WEO datamapper (có forecast năm hiện tại).

    Nguồn:
      - IMF WEO datamapper: GDP growth, CPI, current account (có estimate/forecast)
      - open.er-api.com: tỷ giá USD/VND spot hôm nay

    Returns:
        {
          "gdp_growth":  [{"year": 2024, "value": 6.4, "is_forecast": False}, ...],
          "cpi":         [...],
          "current_acct":[...],
          "usdvnd_spot": float | None,
          "updated":     str,
          "source":      "IMF WEO",
          "error":       str | None,
        }
    """
    import httpx
    from datetime import date

    result: dict = {
        "gdp_growth": [], "cpi": [], "current_acct": [],
        "usdvnd_spot": None,
        "updated": str(date.today()), "source": "IMF WEO", "error": None,
    }

    # IMF WEO Datamapper — không cần key, trả về estimate/forecast năm hiện tại
    IMF_BASE = "https://www.imf.org/external/datamapper/api/v1"
    INDICATORS = {
        "gdp_growth":   "NGDP_RPCH",   # Real GDP growth %
        "cpi":          "PCPIPCH",     # Inflation CPI % change
        "current_acct": "BCA_NGDPD",  # Current account % GDP
    }

    try:
        client = httpx.Client(timeout=15, follow_redirects=True)
        current_year = date.today().year

        for key, ind_code in INDICATORS.items():
            url = f"{IMF_BASE}/{ind_code}/VNM"
            try:
                resp = client.get(url)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                values_map = (
                    payload.get("values", {})
                           .get(ind_code, {})
                           .get("VNM", {})
                )
                series = []
                for yr_str, val in values_map.items():
                    yr  = int(yr_str)
                    v   = _clean_float(val)
                    if v is not None and yr >= current_year - 4:
                        series.append({
                            "year":        yr,
                            "value":       round(v, 2),
                            "is_forecast": yr >= current_year,
                        })
                series.sort(key=lambda x: x["year"], reverse=True)
                result[key] = series
            except Exception:
                continue

        # Tỷ giá spot USD/VND
        try:
            r = client.get("https://open.er-api.com/v6/latest/USD", timeout=8)
            if r.status_code == 200:
                vnd = _clean_float(r.json().get("rates", {}).get("VND"))
                if vnd:
                    result["usdvnd_spot"] = round(vnd, 0)
        except Exception:
            pass

        client.close()

    except Exception as e:
        result["error"] = str(e)

    return result


def get_side_stats(symbol: str, source: str = "VCI") -> dict:
    """Lấy thống kê áp lực mua/bán (buy/sell side stats).

    Returns:
        {
          "buy_vol": float,   # Khối lượng mua chủ động
          "sell_vol": float,  # Khối lượng bán chủ động
          "buy_pct": float,   # % mua
          "sell_pct": float,  # % bán
          "net_vol": float,   # Net = buy - sell
          "raw": dict,        # Raw data từ vnstock
        }
    """
    result = {"buy_vol": None, "sell_vol": None, "buy_pct": None,
              "sell_pct": None, "net_vol": None, "raw": {}}
    try:
        stock = _get_stock(symbol, source)
        df = stock.trading.side_stats()
        if df is None or df.empty:
            return result
        row = df.iloc[0]
        raw = row.to_dict()
        result["raw"] = {k: v for k, v in raw.items()
                         if v is not None and str(v) not in ("nan", "None")}

        # Tìm cột buy/sell linh hoạt theo tên
        def _find(keys):
            for k in keys:
                v = _clean_float(raw.get(k))
                if v is not None:
                    return v
            return None

        buy = _find(["bu", "buy_volume", "buy_vol", "active_buy", "buyVol"])
        sel = _find(["sd", "sell_volume", "sell_vol", "active_sell", "sellVol"])

        if buy is not None and sel is not None:
            total = buy + sel
            result["buy_vol"]  = buy
            result["sell_vol"] = sel
            result["net_vol"]  = buy - sel
            result["buy_pct"]  = round(buy / total * 100, 1) if total else None
            result["sell_pct"] = round(sel / total * 100, 1) if total else None
    except Exception:
        pass
    return result


def get_market_indices() -> list[dict]:
    """Lấy chỉ số thị trường: VN-Index, HNX-Index, UPCOM, VN30.

    Returns list of {index_id, index_value, change, pct_change, trading_date}
    """
    try:
        import io, sys
        from vnstock import Listing
        _old, sys.stdout = sys.stdout, io.StringIO()
        try:
            listing = Listing()
        finally:
            sys.stdout = _old

        df = listing.indices()
        if df is None or df.empty:
            return []

        results = []
        cols = list(df.columns)

        def _col(*names):
            for n in names:
                if n in cols:
                    return n
            return None

        id_col    = _col("index_id", "indexId", "code", "symbol")
        val_col   = _col("index_value", "indexValue", "close", "value", "close_price")
        chg_col   = _col("change", "point_change", "change_point")
        pct_col   = _col("pct_change", "percent_change", "change_percent", "change_pct")
        date_col  = _col("trading_date", "date", "time")

        _TARGET = {"VNINDEX", "HNXINDEX", "UPCOMINDEX", "VN30", "HNX30"}
        for _, row in df.iterrows():
            idx_id = str(row.get(id_col, "")).upper() if id_col else ""
            if _TARGET and idx_id not in _TARGET:
                continue
            results.append({
                "index_id":     idx_id,
                "index_value":  _clean_float(row.get(val_col))  if val_col  else None,
                "change":       _clean_float(row.get(chg_col))  if chg_col  else None,
                "pct_change":   _clean_float(row.get(pct_col))  if pct_col  else None,
                "trading_date": str(row.get(date_col, ""))[:10] if date_col  else "",
            })
        return results
    except Exception:
        return []


def get_capital_history(symbol: str, source: str = "VCI") -> list[dict]:
    """Lấy lịch sử tăng vốn điều lệ / phát hành thêm cổ phiếu.

    Returns list of {date, event_type, charter_capital, issue_share, ratio, notes}
    """
    try:
        stock = _get_stock(symbol, source)
        df = stock.company.capital_history()
        if df is None or df.empty:
            return []
        cols = list(df.columns)

        def _col(*names):
            for n in names:
                if n in cols:
                    return n
            return None

        date_col  = _col("issue_date", "date", "exercise_date", "public_date")
        type_col  = _col("issue_method", "event_type", "type", "method")
        cap_col   = _col("charter_capital", "new_capital", "capital_after")
        share_col = _col("issue_share", "shares_issued", "quantity", "volume")
        ratio_col = _col("ratio", "issue_ratio", "rate")
        note_col  = _col("notes", "description", "title", "details")

        results = []
        for _, row in df.iterrows():
            results.append({
                "date":            str(row.get(date_col, ""))[:10] if date_col else "",
                "event_type":      str(row.get(type_col, ""))      if type_col else "",
                "charter_capital": _clean_float(row.get(cap_col))  if cap_col  else None,
                "issue_share":     _clean_float(row.get(share_col))if share_col else None,
                "ratio":           _clean_float(row.get(ratio_col))if ratio_col else None,
                "notes":           str(row.get(note_col, ""))       if note_col  else "",
            })
        return results[:20]
    except Exception:
        return []
