"""Fetch và cache dữ liệu vĩ mô và thị trường cho Tab Cơ Bản."""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_DATA_DIR    = Path(__file__).parent.parent / "data"
_CACHE_PATH  = _DATA_DIR / "macro_cache.json"
_STATIC_PATH = _DATA_DIR / "macro_static.json"

# TTL: 15 phút cho real-time, 6 giờ cho World Bank (lag 1 năm, không cần cập nhật thường)
_REALTIME_TTL  = 900
_WB_TTL        = 21600


def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    try:
        _CACHE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def _is_fresh(ts_str: str, ttl: int = _REALTIME_TTL) -> bool:
    if not ts_str:
        return False
    try:
        ts = datetime.fromisoformat(ts_str)
        return (datetime.now() - ts).total_seconds() < ttl
    except Exception:
        return False


def fetch_global_market(force: bool = False) -> dict:
    """Lấy dữ liệu thị trường toàn cầu qua yfinance (DXY, Oil, Gold, USD/VND, S&P500)."""
    cache = _load_cache()
    gm = cache.get("global_market", {})
    if not force and _is_fresh(gm.get("fetched_at", "")):
        return gm

    try:
        import yfinance as yf
    except ImportError:
        return {}

    symbols = {
        "dxy":    ("DX-Y.NYB",  "USD Index (DXY)"),
        "oil":    ("BZ=F",      "Dầu Brent (USD/thùng)"),
        "gold":   ("GC=F",      "Vàng (USD/oz)"),
        "usdvnd": ("USDVND=X",  "Tỷ giá USD/VND"),
        "sp500":  ("^GSPC",     "S&P 500"),
    }

    result: dict = {"fetched_at": datetime.now().isoformat()}
    for key, (ticker, label) in symbols.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if hist.empty:
                result[key] = {"label": label, "value": None, "chg_pct": None}
                continue
            last = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last
            chg_pct = (last - prev) / prev * 100 if prev else 0.0
            result[key] = {"label": label, "value": last, "chg_pct": chg_pct}
        except Exception:
            result[key] = {"label": label, "value": None, "chg_pct": None}

    cache["global_market"] = result
    _save_cache(cache)
    return result


def fetch_vnindex_stats(force: bool = False) -> dict:
    """Lấy thống kê VNINDEX: giá, thay đổi 1D/1W/1M, khối lượng.

    Dùng VPS chart API (histdatafeed.vps.com.vn) — nhanh, không cần key,
    trả về OHLCV daily chính xác. Fallback: vnstock VCI.
    """
    cache = _load_cache()
    vi = cache.get("vnindex_stats", {})
    if not force and _is_fresh(vi.get("fetched_at", "")):
        return vi

    result: dict = {"fetched_at": datetime.now().isoformat()}

    # Thử VPS chart API trước (nhanh, ổn định hơn vnstock VCI)
    try:
        import requests as _req
        end_ts = int(time.time())
        start_ts = end_ts - 86400 * 35  # 35 ngày để tính 20D change
        r = _req.get(
            "https://histdatafeed.vps.com.vn/tradingview/history"
            f"?symbol=VNINDEX&resolution=D&from={start_ts}&to={end_ts}",
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            closes  = data.get("c", [])
            volumes = data.get("v", [])
            if closes:
                last_price = float(closes[-1])
                result["price"] = last_price

                def _chg(n):
                    if len(closes) > n:
                        p = float(closes[-(n + 1)])
                        return (last_price - p) / p * 100 if p else None
                    return None

                result["chg_1d"]  = _chg(1)
                result["chg_5d"]  = _chg(5)
                result["chg_20d"] = _chg(20)
                if volumes:
                    result["last_vol"]   = float(volumes[-1])
                    result["avg_vol_5d"] = float(sum(volumes[-5:]) / min(len(volumes), 5))
                result["source"] = "VPS"
    except Exception:
        pass

    # Fallback: vnstock VCI nếu VPS không thành công
    if not result.get("price"):
        try:
            from vnstock import Vnstock
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
            h = Vnstock().stock(symbol="VNINDEX", source="VCI").quote.history(
                start=start, end=end, interval="1D"
            )
            if h is not None and not h.empty:
                close  = h["close"].dropna()
                volume = h["volume"].dropna() if "volume" in h.columns else None
                if len(close) >= 1:
                    last_price = float(close.iloc[-1])
                    result["price"] = last_price

                    def _chg2(n):
                        if len(close) > n:
                            p = float(close.iloc[-(n + 1)])
                            return (last_price - p) / p * 100 if p else None
                        return None

                    result["chg_1d"]  = _chg2(1)
                    result["chg_5d"]  = _chg2(5)
                    result["chg_20d"] = _chg2(20)
                    if volume is not None and len(volume) >= 1:
                        result["last_vol"]   = float(volume.iloc[-1])
                        result["avg_vol_5d"] = float(volume.tail(5).mean())
                    result["source"] = "VCI"
        except Exception:
            pass

    cache["vnindex_stats"] = result
    _save_cache(cache)
    return result


def fetch_foreign_flow(force: bool = False) -> dict:
    """Lấy khối ngoại mua/bán ròng intraday qua vnstock VCI price_board (VN30 basket).

    Trả về tổng giá trị mua/bán ròng của khối ngoại trên ~30 mã lớn (VNĐ).
    Lag: intraday (dữ liệu phiên đang diễn ra hoặc phiên cuối).
    """
    cache = _load_cache()
    ff = cache.get("foreign_flow", {})
    if not force and _is_fresh(ff.get("fetched_at", "")):
        return ff

    result: dict = {"fetched_at": datetime.now().isoformat()}

    # VN30 basket (30 mã vốn hóa lớn nhất HOSE)
    VN30 = [
        "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR",
        "HDB", "HPG", "LPB", "MBB", "MSN", "MWG", "PLX", "POW",
        "SAB", "SHB", "SSB", "SSI", "STB", "TCB", "TPB", "VCB",
        "VHM", "VIB", "VIC", "VJC", "VNM", "VPB",
    ]

    try:
        from vnstock import Trading
        t = Trading(source="VCI", symbol=VN30[0])
        board = t.price_board(symbols_list=VN30)

        if board is not None and not board.empty:
            # MultiIndex columns → flatten
            if hasattr(board.columns, "levels"):
                board.columns = ["_".join(c).strip("_") for c in board.columns]

            buy_col  = next((c for c in board.columns if "foreign_buy_value"  in c.lower()), None)
            sell_col = next((c for c in board.columns if "foreign_sell_value" in c.lower()), None)

            if buy_col and sell_col:
                total_buy  = float(board[buy_col].fillna(0).sum())
                total_sell = float(board[sell_col].fillna(0).sum())
                result.update({
                    "buy_vnd":  total_buy,
                    "sell_vnd": total_sell,
                    "net_vnd":  total_buy - total_sell,
                    "basket":   "VN30",
                    "n_stocks": len(VN30),
                })
    except Exception:
        pass

    cache["foreign_flow"] = result
    _save_cache(cache)
    return result


def fetch_wb_macro(force: bool = False) -> dict:
    """Lấy GDP growth, CPI, current account từ World Bank API.

    Lag: ~1 năm (dữ liệu năm 2025 sẽ có từ Q1/2026).
    Free, không cần key. Timeout phải >=30s (server WB chậm).
    Cache 6 tiếng.
    """
    cache = _load_cache()
    wb = cache.get("wb_macro", {})
    if not force and _is_fresh(wb.get("fetched_at", ""), ttl=_WB_TTL):
        return wb

    result: dict = {"fetched_at": datetime.now().isoformat()}

    INDICATORS = {
        "gdp_growth":    ("NY.GDP.MKTP.KD.ZG", "Tăng trưởng GDP (% YoY)"),
        "cpi":           ("FP.CPI.TOTL.ZG",    "CPI lạm phát (% YoY)"),
        "trade_balance": ("BN.CAB.XOKA.CD",    "Cán cân thương mại (USD)"),
    }

    try:
        import requests as _req
        for key, (ind_code, label) in INDICATORS.items():
            try:
                url = f"https://api.worldbank.org/v2/country/VN/indicator/{ind_code}?format=json&mrv=5"
                r = _req.get(url, timeout=30)
                if r.status_code == 200:
                    records = [x for x in r.json()[1] if x["value"] is not None]
                    if records:
                        result[key] = {
                            "value": records[0]["value"],
                            "year":  records[0]["date"],
                            "label": label,
                        }
            except Exception:
                pass
    except Exception:
        pass

    cache["wb_macro"] = result
    _save_cache(cache)
    return result


def load_static_macro() -> dict:
    """Load dữ liệu vĩ mô VN tĩnh (lãi suất, M2, FDI...) từ file JSON.

    File cập nhật thủ công theo quý. Nếu file chưa tồn tại, dùng default.
    """
    if _STATIC_PATH.exists():
        try:
            return json.loads(_STATIC_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _default_static_macro()


def _default_static_macro() -> dict:
    """Dữ liệu vĩ mô VN — các chỉ số không có free real-time API (cập nhật thủ công theo quý)."""
    return {
        "updated_at": "2026-07-01",
        "source_note": "NHNN, GSO, MPI — cập nhật thủ công theo quý",
        "items": [
            {
                "key": "rate_refin",
                "label": "Lãi suất tái cấp vốn",
                "value": 4.50,
                "unit": "%/năm",
                "period": "06/2025",
                "note": "NHNN duy trì; lãi suất giảm → tích cực cho TTCK",
                "source": "NHNN",
            },
            {
                "key": "rate_deposit_12m",
                "label": "Lãi suất huy động 12T (TB)",
                "value": 5.20,
                "unit": "%/năm",
                "period": "Q1/2026",
                "note": "Lãi tiết kiệm thấp → dịch chuyển tiền vào TTCK",
                "source": "NHNN",
            },
            {
                "key": "m2_growth",
                "label": "Tăng trưởng M2",
                "value": 11.5,
                "unit": "% YoY",
                "period": "2025",
                "note": "M2 cao → thanh khoản dồi dào",
                "source": "NHNN",
            },
            {
                "key": "credit_growth",
                "label": "Tăng trưởng tín dụng",
                "value": 15.08,
                "unit": "% YoY",
                "period": "2025",
                "note": "Mục tiêu 2026: 16%",
                "source": "NHNN",
            },
            {
                "key": "fdi_disbursed",
                "label": "FDI giải ngân",
                "value": 25.35,
                "unit": "tỷ USD",
                "period": "2025",
                "note": "Cao nhất lịch sử → hỗ trợ khu công nghiệp/BĐS KCN",
                "source": "MPI",
            },
            {
                "key": "public_invest",
                "label": "Giải ngân đầu tư công",
                "value": 62.3,
                "unit": "% kế hoạch",
                "period": "2025",
                "note": "Động lực cho xây dựng/vật liệu/hạ tầng",
                "source": "MOF",
            },
        ],
    }


def save_static_macro(data: dict) -> None:
    """Lưu dữ liệu vĩ mô tĩnh (dùng khi cập nhật thủ công)."""
    _DATA_DIR.mkdir(exist_ok=True)
    _STATIC_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_macro_summary(force: bool = False) -> dict:
    """Tổng hợp tất cả dữ liệu macro + thị trường cho Tab Cơ Bản."""
    return {
        "global_market": fetch_global_market(force=force),
        "vnindex_stats": fetch_vnindex_stats(force=force),
        "static_macro":  load_static_macro(),
        "wb_macro":      fetch_wb_macro(force=force),
        "foreign_flow":  fetch_foreign_flow(force=force),
    }
