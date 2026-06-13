"""Screener: scan nhiều mã, lưu/đọc cache, lọc theo tín hiệu."""
import json
import math
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import CACHE_FILE, DEFAULT_SOURCE, DEFAULT_WATCHLIST
from .data import get_price_history, get_price_board
from .indicators import add_all_indicators, get_latest_signals

# Đường dẫn Amibroker
_AMI_SCAN   = Path(os.getenv("AMIBROKER_SCAN_CSV", r"C:\AmibrokerData\scan_result.csv"))
_AMI_DIR    = Path(os.getenv("AMIBROKER_HIST_DIR", r"C:\AmibrokerData\history_by_ticker"))


CACHE_PATH = Path(CACHE_FILE)


def load_cache() -> list[dict]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return []


def save_cache(data: list[dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def scan_symbol(symbol: str, source: str = DEFAULT_SOURCE) -> Optional[dict]:
    """Scan 1 mã, trả dict với đầy đủ signals. None nếu lỗi."""
    try:
        df = get_price_history(symbol, days=40, source=source)
        if df is None or len(df) < 15:
            return None
        df = add_all_indicators(df)
        sig = get_latest_signals(df)
        return {"symbol": symbol, **sig}
    except Exception:
        return None


def scan_watchlist(
    symbols: Optional[list[str]] = None,
    source: str = DEFAULT_SOURCE,
    delay: float = 0.1,
    progress_callback=None,
) -> list[dict]:
    """Scan toàn bộ watchlist và lưu cache.

    progress_callback(i, total, symbol) — dùng cho progress bar Streamlit.
    """
    symbols = symbols or DEFAULT_WATCHLIST
    results = []
    for i, sym in enumerate(symbols):
        if progress_callback:
            progress_callback(i, len(symbols), sym)
        rec = scan_symbol(sym, source=source)
        if rec:
            results.append(rec)
        time.sleep(delay)
    save_cache(results)
    return results


def refresh_prices(source: str = DEFAULT_SOURCE) -> list[dict]:
    """Cập nhật nhanh giá hiện tại cho cache hiện có (không tính lại signal)."""
    cache = load_cache()
    if not cache:
        return []
    symbols = [r["symbol"] for r in cache]
    try:
        board = get_price_board(symbols, source=source)
        price_map = {}
        if "close_price" in board.columns and "symbol" in board.columns:
            for _, row in board.iterrows():
                sym = row.get("symbol")
                price = row.get("close_price")
                if sym and price and not (isinstance(price, float) and math.isnan(price)):
                    price_map[str(sym).upper()] = float(price)
    except Exception:
        price_map = {}

    for rec in cache:
        sym = rec["symbol"]
        if sym in price_map:
            rec["close"] = price_map[sym]

    save_cache(cache)
    return cache


def get_ami_watchlist() -> list[str]:
    """Đọc danh sách mã từ scan_result.csv của Amibroker Explorer.
    Đọc từng dòng lấy field đầu tiên để tránh lỗi do dấu phẩy trong số."""
    if not _AMI_SCAN.exists():
        return DEFAULT_WATCHLIST
    try:
        tickers = []
        with open(_AMI_SCAN, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                first = line.split(",")[0].strip()
                if i == 0 or not first or first.upper() == "TICKER":
                    continue
                tickers.append(first.upper())
        return tickers if tickers else DEFAULT_WATCHLIST
    except Exception:
        return DEFAULT_WATCHLIST


def _parse_ami_date(date_val) -> str:
    s = str(int(date_val)).zfill(8)
    return f"20{s[2:4]}-{s[4:6]}-{s[6:8]}"


def scan_ami_symbol(symbol: str, with_lstm: bool = False) -> Optional[dict]:
    """Scan 1 mã từ Amibroker history_by_ticker — không cần vnstock.
    with_lstm=True: thêm ai_score từ LSTM vào kết quả."""
    path = _AMI_DIR / f"{symbol.upper()}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, header=0)
        df.columns = [c.strip() for c in df.columns]
        df["Date"] = pd.to_datetime(df["Date"].apply(_parse_ami_date), errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"])
        if len(df) < 15:
            return None
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                 "Close": "close", "Volume": "volume"})
        df = add_all_indicators(df)
        sig = get_latest_signals(df)
        rec = {"symbol": symbol, **sig}

        if with_lstm:
            try:
                from .lstm import predict as _lstm_predict, model_ready as _model_ready
                if _model_ready():
                    lr = _lstm_predict(symbol)
                    if lr:
                        rec["ai_score"]        = lr["ai_score"]
                        rec["lstm_signal"]     = lr["signal"]
                        rec["confidence_t5"]   = lr["confidence_t5"]
                        rec["confidence_t10"]  = lr["confidence_t10"]
                        rec["confidence_t25"]  = lr["confidence_t25"]
                        rec["model_version"]   = lr["model_version"]
            except Exception:
                pass

        return rec
    except Exception:
        return None


def scan_ami_watchlist(
    symbols: Optional[list[str]] = None,
    with_lstm: bool = False,
    progress_callback=None,
) -> list[dict]:
    """Scan danh sách mã từ Amibroker data (nhanh, không gọi vnstock).
    with_lstm=True: kèm LSTM inference cho mỗi mã."""
    symbols = symbols or get_ami_watchlist()
    results = []
    for i, sym in enumerate(symbols):
        if progress_callback:
            progress_callback(i, len(symbols), sym)
        rec = scan_ami_symbol(sym, with_lstm=with_lstm)
        if rec:
            results.append(rec)
    save_cache(results)
    return results


def filter_cache(
    signal: Optional[str] = None,
    risk: Optional[str] = None,
    phase: Optional[str] = None,
) -> list[dict]:
    """Lọc cache theo tín hiệu, rủi ro, giai đoạn."""
    data = load_cache()
    if signal:
        data = [r for r in data if r.get("signal") == signal]
    if risk:
        data = [r for r in data if r.get("risk") == risk]
    if phase:
        data = [r for r in data if r.get("phase") == phase]
    return sorted(data, key=lambda x: x.get("tech_score", 0), reverse=True)
