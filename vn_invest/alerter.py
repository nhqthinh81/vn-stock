"""
Alerter — kết hợp tín hiệu Amibroker + LSTM + kỹ thuật, lọc spam, gửi Telegram.

Flow:
    1. Đọc scan_result.csv (Amibroker Explorer đã lọc sơ bộ)
    2. Với mỗi mã: tính composite_score từ AMI + LSTM + Tech
    3. Lọc chỉ giữ tín hiệu chất lượng cao
    4. Spam filter: không re-alert cùng mã+tín hiệu trong 3 ngày
    5. Gửi Telegram với message định dạng đẹp
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("vn_invest.alerter")

# ── Cấu hình ─────────────────────────────────────────────────────────────────
_AMI_SCAN       = Path(os.getenv("AMIBROKER_SCAN_CSV", r"C:\AmibrokerData\scan_result.csv"))
_ALERT_HISTORY  = Path(__file__).parent.parent / "data" / "alert_history.json"
_COOLDOWN_DAYS  = int(os.getenv("ALERT_COOLDOWN_DAYS", "3"))

# Ngưỡng composite score để alert
_BUY_THRESHOLD  = float(os.getenv("ALERT_BUY_THRESHOLD",  "65"))  # >= 65 → BUY alert
_SELL_THRESHOLD = float(os.getenv("ALERT_SELL_THRESHOLD", "35"))  # <= 35 → SELL alert

# Trọng số composite score
_W_AMI  = 0.40
_W_LSTM = 0.40
_W_TECH = 0.20


# ── Parse scan_result.csv ────────────────────────────────────────────────────

def _parse_ami_row(fields: list[str]) -> Optional[dict]:
    """
    Parse 1 dòng CSV của Amibroker. Vấn đề: Vol và Vol10 có dấu phẩy ngàn
    (ví dụ 2,735,300) làm lệch cột. Giải pháp: dò tìm PctChange (float với dấu chấm)
    sau field Close để xác định vị trí thực.
    """
    try:
        ticker = fields[0].strip().upper()
        if not ticker or ticker == "TICKER":
            return None
        close = float(fields[2])

        # Tìm PctChange: field đầu tiên sau cột 3 có dấu chấm thập phân
        pct_idx = None
        for i in range(3, min(len(fields), 10)):
            try:
                val = float(fields[i])
                # PctChange thường nhỏ (-20 đến +20) và có dấu chấm hoặc là số âm
                if "." in fields[i] or (val < 0 and abs(val) < 50):
                    pct_idx = i
                    break
            except ValueError:
                continue

        if pct_idx is None:
            return None

        pct_change = float(fields[pct_idx])

        # Vol10 bắt đầu từ pct_idx+1, cũng có thể có dấu phẩy ngàn
        # Rec và Score nằm sau Vol10 — tìm bằng cách dò tương tự
        rec_idx = None
        for i in range(pct_idx + 1, min(len(fields), pct_idx + 8)):
            try:
                v = int(fields[i])
                if 1 <= v <= 10:  # Rec thường 1-5
                    # Kiểm tra field kế là Score (0-100)
                    if i + 1 < len(fields):
                        s = int(fields[i + 1])
                        if 0 <= s <= 100:
                            rec_idx = i
                            break
            except ValueError:
                continue

        rec   = int(fields[rec_idx])     if rec_idx is not None else 1
        score = int(fields[rec_idx + 1]) if rec_idx is not None else 50

        return {
            "ticker":     ticker,
            "close":      close,
            "pct_change": pct_change,
            "ami_rec":    rec,       # 1-5
            "ami_score":  score,     # 0-100
        }
    except Exception:
        return None


def load_ami_scan_full() -> list[dict]:
    """Đọc toàn bộ scan_result.csv, trả list dict với ticker, close, pct_change, ami_rec, ami_score."""
    if not _AMI_SCAN.exists():
        logger.warning("scan_result.csv không tồn tại: %s", _AMI_SCAN)
        return []
    results = []
    try:
        with open(_AMI_SCAN, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue
                fields = line.strip().split(",")
                rec = _parse_ami_row(fields)
                if rec:
                    results.append(rec)
    except Exception as e:
        logger.error("Lỗi đọc scan_result.csv: %s", e)
    return results


# ── Composite score ──────────────────────────────────────────────────────────

def composite_score(ami_score: float, lstm_score: Optional[float], tech_score: float) -> float:
    """Tính điểm tổng hợp 0-100. Nếu không có LSTM thì chia lại trọng số."""
    if lstm_score is not None:
        return round(ami_score * _W_AMI + lstm_score * _W_LSTM + tech_score * _W_TECH, 1)
    # Không có LSTM: AMI 60%, Tech 40%
    return round(ami_score * 0.60 + tech_score * 0.40, 1)


def signal_label(score: float) -> str:
    if score >= 75: return "BUY-A"
    if score >= 60: return "BUY-B"
    if score >= 40: return "HOLD"
    if score >= 25: return "SELL-B"
    return "SELL-A"


def is_buy_signal(score: float) -> bool:
    return score >= _BUY_THRESHOLD


def is_sell_signal(score: float) -> bool:
    return score <= _SELL_THRESHOLD


# ── Alert history (spam filter) ───────────────────────────────────────────────

def _load_history() -> dict:
    if _ALERT_HISTORY.exists():
        try:
            return json.loads(_ALERT_HISTORY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_history(history: dict) -> None:
    _ALERT_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    _ALERT_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def should_alert(symbol: str, signal: str, history: dict) -> bool:
    """
    Trả True nếu nên gửi alert cho mã này.
    Không gửi nếu cùng symbol+signal đã alert trong COOLDOWN_DAYS ngày.
    """
    key = f"{symbol}_{signal}"
    if key not in history:
        return True
    last_str = history[key].get("sent_at", "")
    try:
        last_dt = datetime.fromisoformat(last_str)
        return datetime.now() - last_dt > timedelta(days=_COOLDOWN_DAYS)
    except Exception:
        return True


def mark_sent(symbol: str, signal: str, score: float, history: dict) -> None:
    key = f"{symbol}_{signal}"
    history[key] = {
        "sent_at":  datetime.now().isoformat(),
        "signal":   signal,
        "score":    score,
    }


# ── Telegram ─────────────────────────────────────────────────────────────────

def _telegram_creds() -> tuple[str, str]:
    token   = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    return token, chat_id


def send_telegram(message: str) -> bool:
    token, chat_id = _telegram_creds()
    if not token or not chat_id:
        logger.warning("Chưa cấu hình TELEGRAM_TOKEN / TELEGRAM_CHAT_ID trong .env")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error("Gửi Telegram thất bại: %s", e)
        return False


def format_message(
    symbol: str,
    signal: str,
    comp_score: float,
    ami_score: float,
    ami_rec: int,
    lstm_result: Optional[dict],
    tech: dict,
    close: float,
    pct_change: float,
) -> str:
    _SIGNAL_ICON = {
        "BUY-A":  "🟢", "BUY-B": "🟩",
        "HOLD":   "🟡",
        "SELL-B": "🟠", "SELL-A": "🔴",
    }
    _RISK_ICON = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}
    _PHASE_ICON = {
        "Accumulation": "📦", "Markup": "📈",
        "Distribution": "📤", "Markdown": "📉", "Neutral": "➖",
    }

    icon   = _SIGNAL_ICON.get(signal, "⚪")
    pct_s  = f"+{pct_change:.2f}%" if pct_change >= 0 else f"{pct_change:.2f}%"
    now_s  = datetime.now().strftime("%d/%m %H:%M")

    lines = [
        f"{icon} <b>{signal} — {symbol}</b>   [{now_s}]",
        f"💰 Giá: <b>{close:,.0f}</b>  ({pct_s})",
        f"",
        f"📊 Điểm tổng hợp: <b>{comp_score:.0f}/100</b>",
        f"   • AMI: {ami_score} (Rec={ami_rec}) | KT: {tech.get('tech_score', 0):.0f}",
    ]

    if lstm_result:
        lstm_s = lstm_result.get("ai_score", 0)
        t5  = lstm_result.get("confidence_t5",  0) * 100
        t10 = lstm_result.get("confidence_t10", 0) * 100
        t25 = lstm_result.get("confidence_t25", 0) * 100
        lines.append(f"   • LSTM: {lstm_s:.0f}  (T+5:{t5:.0f}% T+10:{t10:.0f}% T+25:{t25:.0f}%)")
    else:
        lines.append(f"   • LSTM: N/A")

    lines += [
        f"",
        f"📉 RSI: {tech.get('rsi', 0):.1f}  |  Dist EMA34: {tech.get('dist_ema34_pct', 0):+.2f}%",
        f"📈 Giai đoạn: {_PHASE_ICON.get(tech.get('phase',''), '')} {tech.get('phase', '—')}  |  Rủi ro: {_RISK_ICON.get(tech.get('risk',''), '')} {tech.get('risk', '—')}",
    ]

    return "\n".join(lines)


# ── Main scan + alert ─────────────────────────────────────────────────────────

def run_alert_scan(
    use_lstm: bool = True,
    progress_callback=None,
    dry_run: bool = False,
) -> dict:
    """
    Quét toàn bộ scan_result.csv, lọc tín hiệu chất lượng, gửi Telegram.

    Args:
        use_lstm:          Có chạy LSTM inference không (chậm hơn, chính xác hơn)
        progress_callback: fn(i, total, symbol) cho progress bar Streamlit
        dry_run:           True = không gửi thật, chỉ trả kết quả

    Returns:
        {
            "scanned": int,
            "qualified": int,       # đạt ngưỡng chất lượng
            "sent": int,            # đã gửi Telegram (qua spam filter)
            "skipped_spam": int,
            "alerts": [list of alert dicts],
        }
    """
    from .screener import scan_ami_symbol

    ami_rows = load_ami_scan_full()
    if not ami_rows:
        return {"scanned": 0, "qualified": 0, "sent": 0, "skipped_spam": 0, "alerts": []}

    history = _load_history()
    lstm_module = None
    if use_lstm:
        try:
            from . import lstm as lstm_module
            if not lstm_module.model_ready():
                lstm_module = None
        except Exception:
            lstm_module = None

    stats = {"scanned": 0, "qualified": 0, "sent": 0, "skipped_spam": 0, "alerts": []}

    for i, row in enumerate(ami_rows):
        symbol = row["ticker"]
        if progress_callback:
            progress_callback(i, len(ami_rows), symbol)

        # Tính tech indicators từ Amibroker history
        tech = scan_ami_symbol(symbol)
        if tech is None:
            continue
        stats["scanned"] += 1

        # LSTM inference
        lstm_result = None
        if lstm_module:
            try:
                lstm_result = lstm_module.predict(symbol)
            except Exception:
                pass

        # Composite score
        lstm_score = lstm_result["ai_score"] if lstm_result else None
        comp = composite_score(row["ami_score"], lstm_score, tech["tech_score"])
        signal = signal_label(comp)

        # Chỉ alert buy hoặc sell rõ ràng
        if not (is_buy_signal(comp) or is_sell_signal(comp)):
            continue
        stats["qualified"] += 1

        alert_rec = {
            "symbol":     symbol,
            "signal":     signal,
            "comp_score": comp,
            "ami_score":  row["ami_score"],
            "ami_rec":    row["ami_rec"],
            "tech_score": tech["tech_score"],
            "lstm_score": lstm_score,
            "close":      row["close"],
            "pct_change": row["pct_change"],
            "rsi":        tech["rsi"],
            "dist_ema34_pct": tech["dist_ema34_pct"],
            "phase":      tech["phase"],
            "risk":       tech["risk"],
        }
        stats["alerts"].append(alert_rec)

        # Spam filter
        if not should_alert(symbol, signal, history):
            stats["skipped_spam"] += 1
            continue

        # Gửi Telegram
        msg = format_message(
            symbol=symbol, signal=signal, comp_score=comp,
            ami_score=row["ami_score"], ami_rec=row["ami_rec"],
            lstm_result=lstm_result, tech=tech,
            close=row["close"], pct_change=row["pct_change"],
        )

        if not dry_run:
            ok = send_telegram(msg)
            if ok:
                mark_sent(symbol, signal, comp, history)
                stats["sent"] += 1
                time.sleep(0.3)  # tránh flood Telegram
        else:
            stats["sent"] += 1  # trong dry_run đếm như đã gửi
            mark_sent(symbol, signal, comp, history)

    if not dry_run:
        _save_history(history)

    # Sắp xếp theo composite score giảm dần
    stats["alerts"].sort(key=lambda x: x["comp_score"], reverse=True)
    return stats


def get_alert_history() -> list[dict]:
    """Trả lịch sử alert để hiển thị trong UI."""
    h = _load_history()
    records = []
    for key, val in h.items():
        sym, sig = key.rsplit("_", 1)
        records.append({
            "symbol":  sym,
            "signal":  sig,
            "score":   val.get("score", 0),
            "sent_at": val.get("sent_at", ""),
        })
    return sorted(records, key=lambda x: x["sent_at"], reverse=True)
