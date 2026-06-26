"""Market Regime Filter — xác định xu hướng thị trường VN qua VNI vs SMA50.

Bull  : VNI > SMA50 × 1.01  → ưu tiên BUY signal
Bear  : VNI < SMA50 × 0.99  → ưu tiên SELL signal
Neutral: trong biên ±1% SMA50 → không lọc

Nguồn dữ liệu (ưu tiên theo thứ tự):
  1. File local Amibroker: thử các tên VNI.csv / VNINDEX.csv / ^VNINDEX.csv
  2. Fallback vnstock API: Trading(source='VCI').stock_intraday() → OHLCV lịch sử
     (cache 1 ngày tại data/vni_cache.csv để tránh gọi API lại)
"""
import os
import time
from pathlib import Path

import pandas as pd

_AMI_DIR   = Path(os.getenv("AMIBROKER_HIST_DIR", r"C:\AmibrokerData\history_by_ticker"))
_CACHE_DIR = Path(__file__).parent.parent / "data"
_VNI_CACHE = _CACHE_DIR / "vni_cache.csv"

# Các tên file VNI có thể có trong Amibroker
_VNI_CANDIDATES = ["VNI.csv", "VNINDEX.csv", "^VNINDEX.csv", "VNIDX.csv", "VN-INDEX.csv"]


def _parse_ami_date(date_val) -> str:
    s = str(int(date_val)).zfill(8)
    return f"20{s[2:4]}-{s[4:6]}-{s[6:8]}"


def _add_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Thêm cột sma50 và regime vào df đã có cột Date + close."""
    df = df.sort_values("Date").reset_index(drop=True)
    df["sma50"] = df["close"].rolling(50).mean()
    df["regime"] = "neutral"
    df.loc[df["close"] > df["sma50"] * 1.01, "regime"] = "bull"
    df.loc[df["close"] < df["sma50"] * 0.99, "regime"] = "bear"
    return df


def _load_from_amibroker(ami_dir: Path) -> pd.DataFrame | None:
    """Thử đọc VNI từ nhiều tên file Amibroker khác nhau."""
    for name in _VNI_CANDIDATES:
        path = ami_dir / name
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, header=0)
            df.columns = [c.strip() for c in df.columns]
            df["Date"]  = pd.to_datetime(df["Date"].apply(_parse_ami_date), errors="coerce")
            df = df.dropna(subset=["Date"])
            df["close"] = pd.to_numeric(df.get("Close", df.get("close")), errors="coerce")
            df = df.dropna(subset=["close"])
            if len(df) >= 51:
                return _add_regime(df[["Date", "close"]])
        except Exception:
            continue
    return None


def _load_from_cache() -> pd.DataFrame | None:
    """Đọc VNI cache nếu còn mới (< 24h)."""
    if not _VNI_CACHE.exists():
        return None
    try:
        age_hours = (time.time() - _VNI_CACHE.stat().st_mtime) / 3600
        if age_hours > 24:
            return None
        df = pd.read_csv(_VNI_CACHE, parse_dates=["Date"])
        if len(df) >= 51 and "close" in df.columns:
            return _add_regime(df[["Date", "close"]])
    except Exception:
        pass
    return None


def _fetch_from_vnstock(days: int = 500) -> pd.DataFrame | None:
    """Fetch VNI historical OHLCV từ vnstock (Quote class), lưu cache."""
    try:
        from vnstock import Quote
        end   = pd.Timestamp.today().strftime("%Y-%m-%d")
        start = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        raw   = Quote(symbol="VNINDEX", source="VCI").history(
                    start=start, end=end, interval="1D")
        if raw is None or raw.empty:
            return None

        # vnstock Quote trả cột 'time' + 'close'
        close_col = next((c for c in raw.columns if c.lower() == "close"), None)
        time_col  = next((c for c in raw.columns if c.lower() in ("time", "date")), None)
        if not close_col or not time_col:
            return None

        df = pd.DataFrame({
            "Date":  pd.to_datetime(raw[time_col], errors="coerce"),
            "close": pd.to_numeric(raw[close_col], errors="coerce"),
        }).dropna()

        if len(df) < 51:
            return None

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(_VNI_CACHE, index=False)
        return _add_regime(df)
    except Exception:
        return None


def _load_vni_df(ami_dir: Path | None = None) -> pd.DataFrame | None:
    """Load VNI data từ nguồn tốt nhất: Amibroker local → cache → vnstock API."""
    dir_ = ami_dir or _AMI_DIR

    # 1. Thử Amibroker local (nhanh nhất, chính xác nhất)
    df = _load_from_amibroker(dir_)
    if df is not None:
        return df

    # 2. Thử cache đã lưu (tránh gọi API liên tục)
    df = _load_from_cache()
    if df is not None:
        return df

    # 3. Fallback: fetch từ vnstock API, lưu cache
    return _fetch_from_vnstock()


def get_market_regime(ami_dir: Path | None = None) -> dict:
    """Trả regime thị trường hiện tại dựa trên VNI vs SMA50.

    Returns:
        {
            "regime":      "bull" | "bear" | "neutral",
            "vni_close":   float | None,
            "vni_sma50":   float | None,
            "pct_vs_sma50": float | None,
            "updated_at":  "YYYY-MM-DD" | None,
            "source":      "amibroker" | "cache" | "vnstock" | "unavailable",
        }
    """
    df = _load_vni_df(ami_dir)
    if df is None:
        return {"regime": "neutral", "vni_close": None, "vni_sma50": None,
                "pct_vs_sma50": None, "updated_at": None, "source": "unavailable"}
    last  = df.iloc[-1]
    vni_c = float(last["close"])
    sma50 = float(last["sma50"])
    pct   = (vni_c - sma50) / sma50 * 100

    # Xác định nguồn dữ liệu
    source = "amibroker" if _load_from_amibroker(ami_dir or _AMI_DIR) is not None \
             else ("cache" if _VNI_CACHE.exists() else "vnstock")

    return {
        "regime":       str(last["regime"]),
        "vni_close":    round(vni_c, 2),
        "vni_sma50":    round(sma50, 2),
        "pct_vs_sma50": round(pct, 2),
        "updated_at":   str(last["Date"])[:10],
        "source":       source,
    }


def get_vni_return_series(period: int = 14, ami_dir: Path | None = None) -> "pd.Series[float]":
    """VNI rolling return (period-bar) indexed by date — dùng tính RS per-bar trong backtest."""
    df = _load_vni_df(ami_dir)
    if df is None:
        return pd.Series(dtype=float)
    df["vni_ret"] = df["close"].pct_change(period) * 100
    return df.set_index("Date")["vni_ret"].dropna()


def get_regime_series(ami_dir: Path | None = None) -> "pd.Series[str]":
    """VNI regime theo từng ngày — dùng lookup per-bar trong backtest."""
    df = _load_vni_df(ami_dir)
    if df is None:
        return pd.Series(dtype=str)
    return df.set_index("Date")["regime"]
