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

# Module-level cache cho VNI return series — load 1 lần, dùng lại trong ThreadPoolExecutor
_vni_ret_cache: "pd.Series | None" = None

def _get_vni_rets() -> pd.Series:
    """Lazy-load VNI 14d return series, cache at module level."""
    global _vni_ret_cache
    if _vni_ret_cache is None:
        try:
            from .market_regime import get_vni_return_series
            _vni_ret_cache = get_vni_return_series(14, _AMI_DIR)
        except Exception:
            _vni_ret_cache = pd.Series(dtype=float)
    return _vni_ret_cache


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
        df = get_price_history(symbol, days=120, source=source)
        if df is None or len(df) < 35:
            return None
        df = add_all_indicators(df)
        sig = get_latest_signals(df)
        return {"symbol": symbol, **sig}
    except Exception:
        return None


def apply_live_bar_to_cache(cache: list[dict], board_df) -> list[dict]:
    """Cập nhật cache nhanh bằng price_board — KHÔNG fetch history.

    Dùng 1-step Wilder RSI update và điều chỉnh Dist EMA34 từ % thay đổi giá.
    Phù hợp cho Live mode toàn thị trường (~2-3s thay vì vài phút).

    Công thức:
    - ref_price = price_board.reference_price  (giá tham chiếu = close EOD hôm qua)
    - pct_change = (close - ref_price) / ref_price
    - RSI update (Wilder 1 bước):
        avg_gain, avg_loss ← reverse từ AMI RSI
        new_avg_gain = (avg_gain*13 + max(today_chg, 0)) / 14
        new_avg_loss = (avg_loss*13 + max(-today_chg, 0)) / 14
        new_RSI = 100 - 100 / (1 + new_avg_gain/new_avg_loss)
    - Dist EMA34 update: dist_new ≈ dist_old + pct_change  (EMA34 thay đổi rất ít trong 1 phiên)
    - Signal: tính lại từ tech_score mới với RSI/Dist đã update
    """
    import math as _math
    from .indicators import calculate_tech_score, classify_signal, classify_risk, classify_phase

    # Chuẩn hóa board
    board_df = board_df.copy()
    board_df.columns = [c.lower() for c in board_df.columns]
    board_map: dict[str, dict] = {}
    for _, row in board_df.iterrows():
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue

        def _fv(col, _row=row):
            v = _row.get(col)
            try:
                fv = float(v)
                return None if _math.isnan(fv) else fv
            except (TypeError, ValueError):
                return None

        board_map[sym] = {
            "close":     _fv("close_price"),
            "open":      _fv("open_price"),
            "ref":       _fv("reference_price"),
            "vol_today": _fv("volume_accumulated") or 0,
        }

    result = []
    for rec in cache:
        sym = rec.get("symbol", "")
        b   = board_map.get(sym)
        if not b or not b["close"] or not b["ref"] or b["ref"] == 0:
            result.append(rec)
            continue

        rec  = dict(rec)
        close_new = b["close"]
        ref       = b["ref"]
        today_chg = close_new - ref          # thay đổi tuyệt đối so với EOD hôm qua

        # ── RSI 1-step Wilder update ──────────────────────────────────────
        rsi_old = rec.get("rsi")
        if rsi_old and 0 < rsi_old < 100:
            rs_old    = rsi_old / (100 - rsi_old)   # RS = avg_gain / avg_loss
            # Giả sử avg_loss = 1 (đơn vị tương đối), avg_gain = RS * avg_loss
            # Dùng % change để scale (ref = prev close)
            gain_today = max(today_chg / ref * 100, 0)
            loss_today = max(-today_chg / ref * 100, 0)
            # Ước lượng avg_gain/loss hiện tại từ RS (ATR làm đơn vị)
            atr_est    = rec.get("atr_pct") or 1.5
            avg_loss   = atr_est / 2            # xấp xỉ
            avg_gain   = rs_old * avg_loss
            new_avg_gain = (avg_gain * 13 + gain_today) / 14
            new_avg_loss = (avg_loss * 13 + loss_today) / 14
            if new_avg_loss > 0:
                new_rs  = new_avg_gain / new_avg_loss
                rsi_new = 100 - 100 / (1 + new_rs)
                rec["rsi"] = round(max(0, min(100, rsi_new)), 1)

        # ── Dist EMA34 update ─────────────────────────────────────────────
        dist_old = rec.get("dist_ema34_pct")
        if dist_old is not None:
            pct_today    = today_chg / ref * 100
            rec["dist_ema34_pct"] = round(dist_old + pct_today * 0.97, 2)  # EMA34 dịch chuyển ~3%/bar

        # ── Cập nhật giá + log_return ─────────────────────────────────────
        close_old = rec.get("close") or ref
        rec["close"]      = round(close_new, 2)
        rec["log_return"] = round(_math.log(close_new / close_old) * 100, 3) if close_old else 0

        # ── Tính lại tech_score + signal ─────────────────────────────────
        macd  = rec.get("macd_hist") or 0
        dist  = rec.get("dist_ema34_pct") or 0
        rsi_f = rec.get("rsi") or 50
        wmt   = rec.get("weekly_macd_trend") or 0
        ma_al = rec.get("ma_aligned") or 0
        vol_r = rec.get("volume_ratio") or 1    # giữ từ AMI EOD
        trend = rec.get("price_trend_20d") or 0
        try:
            score  = calculate_tech_score(rsi_f, macd, dist, wmt, ma_al, vol_r, trend)
            signal = classify_signal(score)
            risk   = classify_risk(score, dist)
            phase  = classify_phase(rsi_f, dist)
            rec["tech_score"]    = round(score, 1)
            rec["signal"]        = signal
            rec["signal_class"]  = signal
            rec["risk"]          = risk
            rec["risk_level"]    = risk
            rec["phase"]         = phase
        except Exception:
            pass

        rec["live_updated"] = True
        result.append(rec)

    return result


def scan_symbol_realtime(symbol: str, source: str = DEFAULT_SOURCE,
                         today_bar: Optional[dict] = None) -> Optional[dict]:
    """Scan 1 mã với bar hôm nay (realtime trong phiên).

    today_bar: dict với open/high/low/close/volume đã được fetch trước (batch).
               Nếu None → tự fetch price_board (chậm hơn, chỉ dùng khi scan đơn lẻ).
    """
    try:
        from datetime import date as _date

        if today_bar is None:
            # Fetch đơn lẻ — chỉ dùng cho Tab Kỹ Thuật 1 mã
            from vnstock import Trading
            t = Trading(source="KBS", symbol="VNI")
            board = t.price_board(symbols_list=[symbol])
            if board is None or board.empty:
                return scan_symbol(symbol, source=source)
            board.columns = [c.lower() for c in board.columns]
            row = board[board["symbol"].str.upper() == symbol.upper()]
            if row.empty:
                return scan_symbol(symbol, source=source)
            row = row.iloc[0]

            def _f(col):
                v = row.get(col)
                if v is None: return None
                try:
                    fv = float(v)
                    return None if (fv != fv) else fv
                except (TypeError, ValueError): return None

            today_bar = {
                "open":   _f("open_price"),
                "high":   _f("high_price"),
                "low":    _f("low_price"),
                "close":  _f("close_price"),
                "volume": _f("volume_accumulated") or 0,
            }

        close_p = today_bar.get("close")
        if not close_p:
            return scan_symbol(symbol, source=source)

        # Lấy lịch sử
        df = get_price_history(symbol, days=120, source=source)
        if df is None or len(df) < 35:
            return None

        # Chuẩn hóa tên cột
        df.columns = [c.lower() for c in df.columns]
        today_str = _date.today().isoformat()

        # Kiểm tra bar hôm nay đã có trong lịch sử chưa (tránh duplicate)
        if "time" in df.columns:
            last_date = str(df["time"].iloc[-1])[:10]
        else:
            last_date = ""

        if last_date != today_str:
            new_bar = {
                "time":   today_str,
                "open":   today_bar.get("open")   or close_p,
                "high":   today_bar.get("high")   or close_p,
                "low":    today_bar.get("low")    or close_p,
                "close":  close_p,
                "volume": today_bar.get("volume") or 0,
            }
            df = pd.concat([df, pd.DataFrame([new_bar])], ignore_index=True)

        df = add_all_indicators(df)
        sig = get_latest_signals(df)
        sig["realtime"] = True
        sig["realtime_date"] = today_str
        return {"symbol": symbol, **sig}
    except Exception:
        return scan_symbol(symbol, source=source)


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
                    price_map[str(sym).upper()] = float(price) / 1000  # price_board trả đơn vị đồng, cache dùng nghìn đồng
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


def refresh_signals_from_ami(progress_callback=None) -> list[dict]:
    """Scan lại signal cho toàn bộ mã từ Amibroker scan_result.csv.

    Format V2 (17 cột, có đủ indicators):
      → Tính signal/risk/phase trực tiếp từ Amibroker data (nhanh, không gọi vnstock)
    Format cũ (8 cột):
      → Fallback: đọc history_by_ticker/*.csv
    """
    from .indicators import calculate_tech_score, classify_signal, classify_risk, classify_phase
    import math as _math

    ami_data = get_ami_scan_data()
    cache    = load_cache()

    _sample  = next(iter(ami_data.values()), {}) if ami_data else {}
    use_v2   = bool(_sample.get("ami_v2"))

    if use_v2 and ami_data:
        results = []
        old_map = {r["symbol"]: r for r in cache} if cache else {}
        symbols = list(ami_data.keys())
        total   = len(symbols)
        _nan    = float("nan")

        for idx, sym in enumerate(symbols):
            if progress_callback:
                progress_callback(idx, total, sym)
            d = ami_data[sym]
            close   = d.get("close_ami") or 0.0
            rsi     = d.get("ami_rsi",       _nan)
            macd    = d.get("ami_macd_hist",  _nan)
            dist    = d.get("ami_dist_ema",   _nan)
            atr_pct = d.get("ami_atr_pct",    _nan)
            bb_w    = d.get("ami_bb_width",   _nan)
            vol_r   = d.get("ami_vol_ratio",  _nan)
            ma_al   = int(d.get("ami_ma_al",  0))
            trend20 = float(d.get("ami_trend20", 0.0) or 0.0)
            wmt     = int(d.get("ami_wmt",    0))

            def _s(v): return v if v == v else _nan

            phase  = classify_phase(_s(rsi), _s(dist), trend20, ma_al)
            score  = calculate_tech_score(_s(rsi), _s(macd), _s(dist),
                                          ma_al, _s(vol_r), 999, phase, wmt, _nan)
            signal = classify_signal(score, _s(vol_r), _s(rsi))

            if signal == "BUY-A":
                rsi_ok   = _math.isnan(rsi) or rsi < 65
                wmt_ok   = wmt >= 0
                t20_ok   = trend20 > 0
                phase_ok = phase not in ("Distribution", "Markdown")
                macd_ok  = not _math.isnan(macd) and macd > 0
                if ma_al < 2 or not rsi_ok or not wmt_ok or not t20_ok or not phase_ok or not macd_ok:
                    signal = "BUY-B"

            risk = classify_risk(score, _s(dist), _s(atr_pct), _s(bb_w), _s(vol_r))

            # Field tín hiệu/chỉ báo — phải ghi đè bằng V2 data mới
            # Giữ lại: candle/chart patterns, reversal, reason — V2 chỉ có 1 bar, không tính lại được
            _skip = {
                "close","rsi","macd_hist","dist_ema34_pct","log_return",
                "atr_pct","bb_width_pct","volume_ratio","ma_aligned","price_trend_20d",
                "phase","tech_score","signal","signal_class","risk","risk_level",
                "weekly_macd_trend","composite_score",
                "ami_rec","ami_rec_label","ami_score","ami_setup","ami_forecast","ami_date",
            }
            old = old_map.get(sym, {})
            rec = {k: v for k, v in old.items() if k not in _skip}
            rec.update({
                "symbol":          sym,
                "close":           round(close, 2),
                "rsi":             round(rsi, 1)     if not _math.isnan(rsi)     else None,
                "macd_hist":       round(macd, 4)    if not _math.isnan(macd)    else None,
                "dist_ema34_pct":  round(dist, 1)    if not _math.isnan(dist)    else None,
                "atr_pct":         round(atr_pct, 1) if not _math.isnan(atr_pct) else None,
                "bb_width_pct":    round(bb_w, 1)    if not _math.isnan(bb_w)    else None,
                "volume_ratio":    round(vol_r, 2)   if not _math.isnan(vol_r)   else None,
                "ma_aligned":      ma_al,
                "price_trend_20d": round(trend20, 2),
                "phase":           phase,
                "tech_score":      round(score, 1),
                "signal":             signal,   # key chuẩn cho filter_cache
                "signal_class":       signal,   # giữ cho tương thích
                "risk":               risk,     # key chuẩn cho filter_cache
                "risk_level":         risk,
                "weekly_macd_trend":  wmt,      # từ Amibroker Weekly_MACD — dùng cho BUY-A gate display
                "ami_rec":         d.get("ami_rec"),
                "ami_rec_label":   d.get("ami_rec_label"),
                "ami_score":       d.get("ami_score"),
                "ami_setup":       d.get("ami_setup"),
                "ami_forecast":    d.get("ami_forecast"),
                "ami_date":        d.get("ami_date"),
            })
            # composite_score: tech_score 65% + ami_rec normalized 35%
            _ami_rec  = d.get("ami_rec") or 1
            _ami_norm = (_ami_rec / 3) * 50 + 50   # -3..3 → 0..100
            rec["composite_score"] = round(score * 0.65 + _ami_norm * 0.35, 1)

            # Patterns: V2 không tính được (thiếu OHLCV history) — bổ sung từ history CSV nếu có
            if not rec.get("candle_patterns") and not rec.get("chart_patterns"):
                _hist = scan_ami_symbol(sym)
                if _hist:
                    for _pf in ("candle_patterns", "candle_timeframe", "chart_patterns",
                                "reversal_type", "reversal_strength", "reversal_signals", "reason",
                                "macd_rising", "price_above_sma5_3d", "rs_14d"):
                        if rec.get(_pf) is None:
                            rec[_pf] = _hist.get(_pf)

            results.append(rec)

    else:
        # Fallback: history_by_ticker/*.csv
        symbols = [r["symbol"] for r in cache] if cache else list(ami_data.keys())
        results = []
        total   = len(symbols)
        for idx, sym in enumerate(symbols):
            if progress_callback:
                progress_callback(idx, total, sym)
            rec = scan_ami_symbol(sym)
            if rec:
                extra = ami_data.get(sym, {})
                if extra:
                    rec.update({k: v for k, v in extra.items() if k not in rec or rec[k] is None})
                ami_close = extra.get("close_ami", 0.0)
                if ami_close and ami_close > 0:
                    rec["close"] = ami_close
                rec["ami_date"] = extra.get("ami_date")
                results.append(rec)
            else:
                old = next((r for r in cache if r["symbol"] == sym), None)
                if old:
                    results.append(old)

    if results:
        save_cache(results)
    save_price_refresh_time()
    return results


_AMI_REC_LABELS = {3: "STRONG BUY", 2: "ACCUMULATE", 1: "WATCHING", -2: "RISK SELL", -3: "TOP SELL"}


def get_ami_scan_data() -> dict[str, dict]:
    """Đọc scan_result.csv trả dict {TICKER: {...}}.

    Hỗ trợ 3 format (tự nhận diện qua số cột):
      - Cũ  (6 cột):  Ticker,Date,Close,Vol,Rec,Score
      - V1  (8 cột):  Ticker,Date,Close,Vol,Rec,Score,Setup,Forecast
      - V2 (17 cột):  Ticker,Date,Close,Vol,RSI,MACD_hist,Dist_EMA34,
                       ATR_pct,BB_width,VolRatio,MA_al,Trend20d,Weekly_MACD,
                       Rec,Score,Setup,Forecast
    """
    if not _AMI_SCAN.exists():
        return {}
    result: dict[str, dict] = {}
    _nan = float("nan")
    def _f(parts, idx, default=_nan):
        try:
            v = parts[idx].strip()
            return float(v) if v not in ("", "---") else default
        except (ValueError, IndexError):
            return default

    try:
        with open(_AMI_SCAN, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                parts = line.strip().split(",")
                if i == 0:
                    continue
                if len(parts) < 6:
                    continue
                ticker = parts[0].strip().upper()
                if not ticker or ticker == "TICKER":
                    continue

                is_v2 = len(parts) >= 15  # format mới có ≥15 cột

                close_ami = _f(parts, 2, 0.0)
                date_str  = parts[1].strip() if len(parts) > 1 else ""

                if is_v2:
                    rsi       = _f(parts, 4)
                    macd_hist = _f(parts, 5)
                    dist_ema  = _f(parts, 6)
                    atr_pct   = _f(parts, 7)
                    bb_width  = _f(parts, 8)
                    vol_ratio = _f(parts, 9)
                    ma_al     = int(_f(parts, 10, 0))
                    trend20   = _f(parts, 11, 0.0)
                    wmt       = int(_f(parts, 12, 0))
                    try:
                        rec   = int(_f(parts, 13, 1))
                        score = _f(parts, 14, 0.0)
                    except Exception:
                        rec = 1; score = 0.0
                    setup    = parts[15].strip() if len(parts) > 15 else None
                    forecast = parts[16].strip() if len(parts) > 16 else None
                else:
                    rsi = macd_hist = dist_ema = atr_pct = bb_width = vol_ratio = _nan
                    ma_al = 0; trend20 = 0.0; wmt = 0
                    try:
                        rec   = int(_f(parts, 4, 1))
                        score = _f(parts, 5, 0.0)
                    except Exception:
                        rec = 1; score = 0.0
                    setup    = parts[6].strip() if len(parts) > 6 else None
                    forecast = parts[7].strip() if len(parts) > 7 else None

                result[ticker] = {
                    "close_ami":     close_ami,
                    "ami_date":      date_str,
                    "ami_rsi":       rsi,
                    "ami_macd_hist": macd_hist,
                    "ami_dist_ema":  dist_ema,
                    "ami_atr_pct":   atr_pct,
                    "ami_bb_width":  bb_width,
                    "ami_vol_ratio": vol_ratio,
                    "ami_ma_al":     ma_al,
                    "ami_trend20":   trend20,
                    "ami_wmt":       wmt,
                    "ami_rec":       rec,
                    "ami_rec_label": _AMI_REC_LABELS.get(rec, "WATCHING"),
                    "ami_score":     round(score, 1) if score == score else 0.0,
                    "ami_setup":     setup    if setup    and setup    != "---" else None,
                    "ami_forecast":  forecast if forecast and forecast != "---" else None,
                    "ami_v2":        is_v2,
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
        # Inject VNI return 14d để add_all_indicators tính rs_14d cột
        _vni_rets = _get_vni_rets()
        if not _vni_rets.empty and "Date" in df.columns:
            df["vni_ret_14d"] = df["Date"].map(_vni_rets)
        df = add_all_indicators(df)
        sig = get_latest_signals(df)
        # Tính % thay đổi so phiên trước để dùng trong alert message
        _closes = df["close"].dropna()
        if len(_closes) >= 2:
            sig["pct_change"] = round(
                (_closes.iloc[-1] - _closes.iloc[-2]) / _closes.iloc[-2] * 100, 2
            )
        else:
            sig["pct_change"] = 0.0
        # Sol 5: Composite Score (partial — chưa có ami_rec, sẽ recompute sau khi merge)
        # Trọng số: tech_score 65%, reversal_strength 35%
        _ts  = sig.get("tech_score", 50) or 50
        _rs  = sig.get("reversal_strength", 0) or 0
        sig["composite_score"] = round(_ts * 0.65 + _rs * 0.35, 1)

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


_CHECKPOINT_PATH = CACHE_PATH.with_suffix(".checkpoint.json")


def _load_checkpoint() -> dict:
    if _CHECKPOINT_PATH.exists():
        try:
            return json.loads(_CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_checkpoint(done: list[str], all_syms: list[str], results_map: dict) -> None:
    _CHECKPOINT_PATH.write_text(
        json.dumps({
            "done":        done,
            "all_symbols": all_syms,
            "partial":     list(results_map.values()),
            "saved_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_checkpoint() -> None:
    _CHECKPOINT_PATH.unlink(missing_ok=True)


def scan_ami_watchlist(
    symbols: Optional[list[str]] = None,
    with_lstm: bool = False,
    progress_callback=None,
    max_workers: int = 8,
    resume: bool = True,
) -> list[dict]:
    """Scan danh sách mã từ Amibroker data song song (ThreadPoolExecutor).
    with_lstm=True: kèm LSTM inference — tự động giảm workers=1 vì Keras không thread-safe.
    resume=True: tự động tiếp tục nếu scan bị ngắt giữa chừng (dùng checkpoint).
    """
    symbols = symbols or get_ami_watchlist()
    total = len(symbols)
    workers = 1 if with_lstm else max_workers

    # Resume từ checkpoint nếu có
    results_map: dict[str, dict] = {}
    done_set: set[str] = set()
    if resume:
        ckpt = _load_checkpoint()
        if ckpt.get("all_symbols") == symbols:
            done_set = set(ckpt.get("done", []))
            for rec in ckpt.get("partial", []):
                if "symbol" in rec:
                    results_map[rec["symbol"]] = rec

    pending = [s for s in symbols if s not in done_set]
    done_list = list(done_set)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_ami_symbol, sym, with_lstm): sym for sym in pending}
        for future in as_completed(futures):
            sym = futures[future]
            done_list.append(sym)
            if progress_callback:
                progress_callback(len(done_list) - 1, total, sym)
            try:
                rec = future.result()
                if rec:
                    results_map[sym] = rec
            except Exception:
                pass
            # Ghi checkpoint mỗi 20 mã để có thể resume nếu bị ngắt
            if len(done_list) % 20 == 0:
                _save_checkpoint(done_list, symbols, results_map)

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

            # Sol 5: recompute composite_score đầy đủ với ami_rec
            # ami_rec: -3..3 → normalize → 0..100
            ami_rec       = ami_data[s]["ami_rec"]
            ami_norm      = (ami_rec / 3) * 50 + 50
            ts            = rec.get("tech_score", 50) or 50
            rs            = rec.get("reversal_strength", 0) or 0
            rec["composite_score"] = round(ts * 0.50 + ami_norm * 0.30 + rs * 0.20, 1)

        results.append(rec)

    save_cache(results)
    clear_checkpoint()  # xóa checkpoint khi scan hoàn thành
    return results


_BAD_STATUSES = {"restricted", "suspended", "delisted", "warning"}


def filter_cache(
    signal: Optional[str] = None,
    risk: Optional[str] = None,
    phase: Optional[str] = None,
    ai_score: Optional[str] = None,
    ami_rec: Optional[str] = None,
    setup: Optional[str] = None,
    forecast: Optional[str] = None,
    pattern: Optional[str] = None,
    data: Optional[list[dict]] = None,
    exclude_restricted: bool = True,
) -> list[dict]:
    """Lọc cache theo nhiều tiêu chí.
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
    if ami_rec:
        rows = [r for r in rows if (r.get("ami_rec") or "").strip().upper() == ami_rec.upper()]
    if setup:
        rows = [r for r in rows if setup.upper() in (r.get("ami_setup") or "").upper()]
    if forecast:
        rows = [r for r in rows if forecast.upper() in (r.get("ami_forecast") or "").upper()]
    if ai_score:
        def _ai_ok(r):
            v = r.get("ai_score")
            try:
                v = float(v)
            except (TypeError, ValueError):
                return ai_score == "Có AI Score" and v is not None
            if ai_score == "≥ 70 (Mạnh)":      return v >= 70
            if ai_score == "≥ 50 (Tích cực)":   return v >= 50
            if ai_score == "≤ 30 (Yếu)":        return v <= 30
            if ai_score == "Có AI Score":        return True
            return True
        rows = [r for r in rows if _ai_ok(r)]
    if pattern:
        def _pat_ok(r):
            pats = " ".join([str(r.get("candle_patterns") or ""), str(r.get("chart_patterns") or "")]).lower()
            if pattern == "Có mẫu bull":     return any(k in pats for k in ["bullish","hammer","morning star","dragonfly","three white"])
            if pattern == "Có mẫu bear":     return any(k in pats for k in ["bearish","shooting star","hanging man","evening star","three black"])
            if pattern == "Có mẫu neutral":  return bool(pats.strip()) and not any(k in pats for k in ["bullish","bearish"])
            if pattern == "Không có mẫu":   return not pats.strip()
            return True
        rows = [r for r in rows if _pat_ok(r)]
    # Ưu tiên composite_score nếu có, fallback tech_score
    return sorted(rows, key=lambda x: x.get("composite_score") or x.get("tech_score", 0), reverse=True)
