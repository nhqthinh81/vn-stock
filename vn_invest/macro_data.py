"""Fetch và cache dữ liệu vĩ mô và thị trường cho Tab Cơ Bản."""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_DATA_DIR   = Path(__file__).parent.parent / "data"
_CACHE_PATH = _DATA_DIR / "macro_cache.json"
_STATIC_PATH = _DATA_DIR / "macro_static.json"

# TTL cho real-time data: 15 phút
_REALTIME_TTL = 900


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
    """Lấy thống kê VNINDEX: giá, thay đổi 1D/1W/1M, khối lượng giao dịch."""
    cache = _load_cache()
    vi = cache.get("vnindex_stats", {})
    if not force and _is_fresh(vi.get("fetched_at", "")):
        return vi

    result: dict = {"fetched_at": datetime.now().isoformat()}

    try:
        from vnstock import Vnstock
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
        h = Vnstock().stock(symbol="VNINDEX", source="VCI").quote.history(
            start=start, end=end, interval="1D"
        )
        if h is None or h.empty:
            cache["vnindex_stats"] = result
            _save_cache(cache)
            return result

        close = h["close"].dropna()
        volume = h["volume"].dropna() if "volume" in h.columns else None

        if len(close) < 1:
            cache["vnindex_stats"] = result
            _save_cache(cache)
            return result

        last_price = float(close.iloc[-1])
        result["price"] = last_price

        def _chg(n):
            if len(close) > n:
                p = float(close.iloc[-(n + 1)])
                return (last_price - p) / p * 100 if p else None
            return None

        result["chg_1d"]  = _chg(1)
        result["chg_5d"]  = _chg(5)
        result["chg_20d"] = _chg(20)

        if volume is not None and len(volume) >= 5:
            # Giá trị khớp lệnh TB 5 phiên (tỷ đồng — ước tính: volume * price * 0.001)
            avg_vol_5d = float(volume.tail(5).mean())
            result["avg_vol_5d"] = avg_vol_5d
            result["last_vol"]   = float(volume.iloc[-1]) if len(volume) > 0 else None

    except Exception:
        pass

    cache["vnindex_stats"] = result
    _save_cache(cache)
    return result


def load_static_macro() -> dict:
    """Load dữ liệu vĩ mô VN tĩnh (GDP, CPI, lãi suất...) từ file JSON.

    File được cập nhật thủ công hoặc qua hàm update_static_macro().
    Nếu file chưa tồn tại, trả về dữ liệu mẫu với giá trị gần nhất từ nguồn công khai.
    """
    if _STATIC_PATH.exists():
        try:
            return json.loads(_STATIC_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _default_static_macro()


def _default_static_macro() -> dict:
    """Dữ liệu vĩ mô VN mặc định — cập nhật cuối Q1/2026 từ GSO, NHNN, ADB."""
    return {
        "updated_at": "2026-04-01",
        "source_note": "GSO, NHNN, ADB — cập nhật thủ công theo quý",
        "items": [
            {
                "key": "gdp_growth",
                "label": "Tăng trưởng GDP",
                "value": 7.09,
                "unit": "% YoY",
                "period": "2025 cả năm",
                "note": "Mục tiêu 2026: 8%+",
                "source": "GSO",
            },
            {
                "key": "cpi",
                "label": "CPI (lạm phát)",
                "value": 3.24,
                "unit": "% YoY",
                "period": "Q1/2026",
                "note": "Mục tiêu < 4.5%",
                "source": "GSO",
            },
            {
                "key": "rate_refin",
                "label": "Lãi suất tái cấp vốn",
                "value": 4.50,
                "unit": "%/năm",
                "period": "Tháng 6/2025",
                "note": "NHNN duy trì ổn định",
                "source": "NHNN",
            },
            {
                "key": "rate_deposit_12m",
                "label": "Lãi suất huy động 12 tháng (TB)",
                "value": 5.20,
                "unit": "%/năm",
                "period": "Q1/2026",
                "note": "Áp lực dịch chuyển tiền gửi → TTCK khi lãi giảm",
                "source": "NHNN",
            },
            {
                "key": "m2_growth",
                "label": "Tăng trưởng cung tiền M2",
                "value": 11.5,
                "unit": "% YoY",
                "period": "2025 cả năm",
                "note": "Mục tiêu tín dụng 2026: 16%",
                "source": "NHNN",
            },
            {
                "key": "credit_growth",
                "label": "Tăng trưởng tín dụng",
                "value": 15.08,
                "unit": "% YoY",
                "period": "2025 cả năm",
                "note": "",
                "source": "NHNN",
            },
            {
                "key": "trade_balance",
                "label": "Cán cân thương mại",
                "value": 24.8,
                "unit": "tỷ USD",
                "period": "2025 cả năm",
                "note": "Xuất siêu",
                "source": "GSO",
            },
            {
                "key": "fdi_disbursed",
                "label": "FDI giải ngân",
                "value": 25.35,
                "unit": "tỷ USD",
                "period": "2025 cả năm",
                "note": "Cao nhất lịch sử",
                "source": "MPI",
            },
            {
                "key": "public_invest",
                "label": "Giải ngân đầu tư công",
                "value": 62.3,
                "unit": "% kế hoạch",
                "period": "2025 cả năm",
                "note": "Động lực nhóm xây dựng/vật liệu/hạ tầng",
                "source": "MOF",
            },
        ],
    }


def save_static_macro(data: dict) -> None:
    """Lưu dữ liệu vĩ mô tĩnh (dùng khi user cập nhật thủ công)."""
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
    }
