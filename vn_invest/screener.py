"""Screener: scan nhiều mã, lưu/đọc cache, lọc theo tín hiệu."""
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import CACHE_FILE, DEFAULT_SOURCE, DEFAULT_WATCHLIST, RESTRICTED_SYMBOLS
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


_META_PATH = CACHE_PATH.with_suffix(".meta.json")


def save_cache(data: list[dict], scanned_at: datetime | None = None) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Ghi metadata riêng để không làm thay đổi format cache chính
    meta = {
        "scanned_at":          (scanned_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
        "count":               len(data),
        "price_refreshed_at":  None,
    }
    _META_PATH.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def load_cache_meta() -> dict:
    """Đọc metadata: scanned_at, count, price_refreshed_at."""
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"scanned_at": None, "count": 0, "price_refreshed_at": None}


def save_price_refresh_time() -> None:
    """Cập nhật price_refreshed_at trong metadata sau refresh_prices()."""
    meta = load_cache_meta()
    meta["price_refreshed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _META_PATH.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def scan_symbol(symbol: str, source: str = DEFAULT_SOURCE) -> Optional[dict]:
    """Scan 1 mã, trả dict với đầy đủ signals. None nếu lỗi."""
    try:
        # Fix: 40 calendar days ≈ 28 trading days → MACD(12,26,9) cần 35+ bars để hội tụ
        # SMA50 luôn NaN với <50 bars; nâng lên 120 days ≈ 85 trading days
        df = get_price_history(symbol, days=120, source=source)
        if df is None or len(df) < 35:
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

    save_cache(cache, scanned_at=datetime.strptime(
        load_cache_meta().get("scanned_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "%Y-%m-%d %H:%M:%S"
    ))
    save_price_refresh_time()
    return cache


_AMI_REC_LABELS = {3: "STRONG BUY", 2: "ACCUMULATE", 1: "WATCHING", -2: "RISK SELL", -3: "TOP SELL"}


def get_ami_scan_data() -> dict[str, dict]:
    """Đọc scan_result.csv trả dict {TICKER: {rec, rec_label, ami_score, ami_setup, ami_forecast}}.
    Hỗ trợ cả 2 format:
      - Cũ (6 cột): Ticker,Date,Close,Vol,Rec,Score
      - Mới (8 cột): Ticker,Date,Close,Vol,Rec,Score,Setup,Forecast
    """
    if not _AMI_SCAN.exists():
        return {}
    result: dict[str, dict] = {}
    try:
        with open(_AMI_SCAN, encoding="utf-8", errors="replace") as f:
            header = None
            for i, line in enumerate(f):
                parts = line.strip().split(",")
                if i == 0:
                    header = [p.strip().lower() for p in parts]
                    continue
                if len(parts) < 6:
                    continue
                ticker = parts[0].strip().upper()
                if not ticker or ticker == "TICKER":
                    continue
                try:
                    rec   = int(float(parts[4].strip()))
                    score = float(parts[5].strip())
                except (ValueError, IndexError):
                    rec = 1; score = 0.0
                # Setup và Forecast — chỉ có trong format mới (>= 8 cột)
                setup    = parts[6].strip() if len(parts) > 6 else "---"
                forecast = parts[7].strip() if len(parts) > 7 else "---"
                result[ticker] = {
                    "ami_rec":       rec,
                    "ami_rec_label": _AMI_REC_LABELS.get(rec, "WATCHING"),
                    "ami_score":     round(score, 1),
                    "ami_setup":     setup    if setup    != "---" else None,
                    "ami_forecast":  forecast if forecast != "---" else None,
                }
    except Exception:
        pass
    return result


def get_ami_watchlist() -> list[str]:
    """Đọc danh sách mã từ scan_result.csv của Amibroker Explorer (đã qua lọc).
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


def get_all_ami_symbols() -> list[str]:
    """Đọc toàn bộ mã có file CSV trong history_by_ticker/ — không qua lọc Amibroker Explorer.
    Trả danh sách đầy đủ hơn scan_result.csv (có thể nhiều hơn đáng kể).
    """
    if not _AMI_DIR.exists():
        return DEFAULT_WATCHLIST
    try:
        symbols = sorted(p.stem.upper() for p in _AMI_DIR.glob("*.csv"))
        return symbols if symbols else DEFAULT_WATCHLIST
    except Exception:
        return DEFAULT_WATCHLIST


def get_ami_scan_age() -> str | None:
    """Trả thời gian sửa đổi cuối của scan_result.csv dạng 'X phút trước', hoặc None."""
    if not _AMI_SCAN.exists():
        return None
    try:
        mtime  = _AMI_SCAN.stat().st_mtime
        mins   = int((time.time() - mtime) / 60)
        if mins < 1:   return "vừa xong"
        if mins < 60:  return f"{mins} phút trước"
        hrs = mins // 60
        if hrs < 24:   return f"{hrs} giờ trước"
        return f"{hrs // 24} ngày trước"
    except Exception:
        return None


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

        # Lớp 1: đánh dấu ngay từ blacklist tĩnh
        if symbol.upper() in RESTRICTED_SYMBOLS:
            rec["stock_status"] = "restricted"

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


def _enrich_stock_status(results: list[dict]) -> None:
    """Fetch stock_status động từ vnstock cho các mã tín hiệu BUY (chạy sau scan).
    Chỉ fetch mã chưa có status từ blacklist tĩnh để tiết kiệm thời gian.
    Cập nhật in-place.
    """
    from .data import get_stock_status
    buy_signals = {"BUY-A", "BUY-B"}
    targets = [
        r for r in results
        if r.get("signal") in buy_signals and "stock_status" not in r
    ]
    for rec in targets:
        try:
            status_info = get_stock_status(rec["symbol"])
            st = status_info.get("status", "normal")
            if st != "normal":
                rec["stock_status"] = st
            time.sleep(0.3)
        except Exception:
            pass


def scan_ami_watchlist(
    symbols: Optional[list[str]] = None,
    with_lstm: bool = False,
    progress_callback=None,
    max_workers: int = 8,
) -> list[dict]:
    """Scan danh sách mã từ Amibroker data song song (ThreadPoolExecutor).
    with_lstm=True: kèm LSTM inference — tự động giảm workers=2 vì Keras không thread-safe.
    stock_status đánh dấu từ RESTRICTED_SYMBOLS tĩnh, không fetch vnstock.
    """
    symbols = symbols or get_ami_watchlist()
    total = len(symbols)
    # Keras không thread-safe — bắt buộc dùng 1 worker khi có LSTM để tránh deadlock
    workers = 1 if with_lstm else max_workers
    results_map: dict[str, dict] = {}
    done_count = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_ami_symbol, sym, with_lstm): sym for sym in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            done_count += 1
            if progress_callback:
                progress_callback(done_count - 1, total, sym)
            try:
                rec = future.result()
                if rec:
                    results_map[sym] = rec
            except Exception:
                pass

    # Giữ thứ tự ban đầu; merge Amibroker Rec/Score nếu có
    ami_data = get_ami_scan_data()
    results = []
    for s in symbols:
        if s not in results_map:
            continue
        rec = results_map[s]
        if s in ami_data:
            rec.setdefault("ami_rec",       ami_data[s]["ami_rec"])
            rec.setdefault("ami_rec_label", ami_data[s]["ami_rec_label"])
            rec.setdefault("ami_score",     ami_data[s]["ami_score"])
            if ami_data[s].get("ami_setup"):
                rec.setdefault("ami_setup",    ami_data[s]["ami_setup"])
            if ami_data[s].get("ami_forecast"):
                rec.setdefault("ami_forecast", ami_data[s]["ami_forecast"])
        results.append(rec)
    save_cache(results)
    return results


_BAD_STATUSES = {"restricted", "suspended", "delisted", "warning"}


def filter_cache(
    signal: Optional[str] = None,
    risk: Optional[str] = None,
    phase: Optional[str] = None,
    data: Optional[list[dict]] = None,
    exclude_restricted: bool = True,
) -> list[dict]:
    """Lọc cache theo tín hiệu, rủi ro, giai đoạn.
    exclude_restricted=True (mặc định): loại mã có stock_status restricted/suspended/delisted/warning
    Truyền data= để tránh đọc disk khi đã có trong session_state.
    """
    rows = data if data is not None else load_cache()
    if exclude_restricted:
        rows = [
            r for r in rows
            if r.get("stock_status", "normal") not in _BAD_STATUSES
            and r.get("symbol", "") not in RESTRICTED_SYMBOLS
        ]
    if signal:
        rows = [r for r in rows if r.get("signal") == signal]
    if risk:
        rows = [r for r in rows if r.get("risk") == risk]
    if phase:
        rows = [r for r in rows if r.get("phase") == phase]
    return sorted(rows, key=lambda x: x.get("tech_score", 0), reverse=True)
