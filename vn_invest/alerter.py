"""
Alerter — kết hợp tín hiệu Amibroker + LSTM + kỹ thuật, lọc spam, gửi Telegram.

Flow:
    1. Đọc scan_result.csv qua screener.get_ami_scan_data() (parser duy nhất)
    2. Với mỗi mã: tính composite_score từ AMI + LSTM + Tech
    3. Lọc chỉ giữ tín hiệu chất lượng cao
    4. Spam filter: không re-alert cùng mã+tín hiệu trong COOLDOWN_DAYS ngày
    5. Gửi Telegram với message định dạng đẹp

Auto-trigger: gọi check_and_alert() sau mỗi lần scan Amibroker —
    hàm này kiểm tra mtime của scan_result.csv, chỉ chạy khi file thực sự mới.
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
_LAST_RUN_PATH  = Path(__file__).parent.parent / "data" / "alert_last_run.json"
_COOLDOWN_DAYS  = int(os.getenv("ALERT_COOLDOWN_DAYS", "3"))

# Ngưỡng composite score để alert
_BUY_THRESHOLD  = float(os.getenv("ALERT_BUY_THRESHOLD",  "65"))
_SELL_THRESHOLD = float(os.getenv("ALERT_SELL_THRESHOLD", "35"))

# Trọng số composite score
_W_AMI  = 0.40
_W_LSTM = 0.40
_W_TECH = 0.20


# ── Composite score ──────────────────────────────────────────────────────────

def composite_score(ami_score: float, lstm_score: Optional[float], tech_score: float) -> float:
    """Tính điểm tổng hợp 0-100. Nếu không có LSTM thì chia lại trọng số."""
    if lstm_score is not None:
        return round(ami_score * _W_AMI + lstm_score * _W_LSTM + tech_score * _W_TECH, 1)
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
    Trả True nếu nên gửi alert.
    Không gửi nếu cùng symbol+signal đã alert trong COOLDOWN_DAYS ngày.
    Nếu signal thay đổi (ví dụ HOLD→BUY-A) thì luôn cho qua bất kể cooldown.
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
        "sent_at": datetime.now().isoformat(),
        "signal":  signal,
        "score":   score,
    }


# ── Auto-trigger: kiểm tra mtime scan_result.csv ─────────────────────────────

def _load_last_run() -> dict:
    if _LAST_RUN_PATH.exists():
        try:
            return json.loads(_LAST_RUN_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_last_run(scan_mtime: float) -> None:
    _LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LAST_RUN_PATH.write_text(json.dumps({
        "scan_mtime": scan_mtime,
        "ran_at": datetime.now().isoformat(),
    }, ensure_ascii=False), encoding="utf-8")


def scan_result_is_new() -> bool:
    """Trả True nếu scan_result.csv đã được cập nhật kể từ lần alert cuối."""
    if not _AMI_SCAN.exists():
        return False
    current_mtime = _AMI_SCAN.stat().st_mtime
    last_run = _load_last_run()
    return current_mtime > last_run.get("scan_mtime", 0)


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
            "chat_id":    chat_id,
            "text":       message,
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
    _RISK_ICON  = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}
    _PHASE_ICON = {
        "Accumulation": "📦", "Markup": "📈",
        "Distribution": "📤", "Markdown": "📉", "Neutral": "➖",
    }

    icon  = _SIGNAL_ICON.get(signal, "⚪")
    pct_s = f"+{pct_change:.2f}%" if pct_change >= 0 else f"{pct_change:.2f}%"
    now_s = datetime.now().strftime("%d/%m %H:%M")

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
        f"📈 Giai đoạn: {_PHASE_ICON.get(tech.get('phase',''), '')} {tech.get('phase', '—')}"
        f"  |  Rủi ro: {_RISK_ICON.get(tech.get('risk',''), '')} {tech.get('risk', '—')}",
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

    Dùng screener.get_ami_scan_data() làm parser duy nhất (không có parser riêng).
    close và pct_change lấy từ scan_ami_symbol() (đọc history_by_ticker CSV).

    Args:
        use_lstm:          Có chạy LSTM inference không
        progress_callback: fn(i, total, symbol) cho progress bar Streamlit
        dry_run:           True = không gửi thật, chỉ trả kết quả

    Returns:
        {"scanned", "qualified", "sent", "skipped_spam", "alerts"}
    """
    from .screener import get_ami_scan_data, scan_ami_symbol

    ami_data = get_ami_scan_data()
    if not ami_data:
        return {"scanned": 0, "qualified": 0, "sent": 0, "skipped_spam": 0, "alerts": []}

    symbols = list(ami_data.keys())
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

    for i, symbol in enumerate(symbols):
        if progress_callback:
            progress_callback(i, len(symbols), symbol)

        ami = ami_data[symbol]

        # Tính tech indicators từ Amibroker history CSV
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
        comp   = composite_score(ami["ami_score"], lstm_score, tech["tech_score"])
        signal = signal_label(comp)

        if not (is_buy_signal(comp) or is_sell_signal(comp)):
            continue
        stats["qualified"] += 1

        close      = tech.get("close", ami.get("close_ami", 0))
        pct_change = tech.get("pct_change", 0.0)

        alert_rec = {
            "symbol":        symbol,
            "signal":        signal,
            "comp_score":    comp,
            "ami_score":     ami["ami_score"],
            "ami_rec":       ami["ami_rec"],
            "ami_rec_label": ami["ami_rec_label"],
            "tech_score":    tech["tech_score"],
            "lstm_score":    lstm_score,
            "close":         close,
            "pct_change":    pct_change,
            "rsi":           tech.get("rsi", 0),
            "dist_ema34_pct": tech.get("dist_ema34_pct", 0),
            "phase":         tech.get("phase", ""),
            "risk":          tech.get("risk", ""),
        }
        stats["alerts"].append(alert_rec)

        # Spam filter
        if not should_alert(symbol, signal, history):
            stats["skipped_spam"] += 1
            continue

        msg = format_message(
            symbol=symbol, signal=signal, comp_score=comp,
            ami_score=ami["ami_score"], ami_rec=ami["ami_rec"],
            lstm_result=lstm_result, tech=tech,
            close=close, pct_change=pct_change,
        )

        if not dry_run:
            ok = send_telegram(msg)
            if ok:
                mark_sent(symbol, signal, comp, history)
                stats["sent"] += 1
                time.sleep(0.3)  # tránh flood Telegram API
        else:
            stats["sent"] += 1
            mark_sent(symbol, signal, comp, history)

    if not dry_run:
        _save_history(history)

    stats["alerts"].sort(key=lambda x: x["comp_score"], reverse=True)
    return stats


def check_and_alert(use_lstm: bool = False, dry_run: bool = False) -> Optional[dict]:
    """
    Auto-trigger: chỉ chạy nếu scan_result.csv đã cập nhật kể từ lần cuối.
    Gọi hàm này sau mỗi lần Amibroker Explorer chạy xong.

    Trả None nếu file chưa mới. Trả stats dict nếu đã chạy alert.
    """
    if not scan_result_is_new():
        return None

    scan_mtime = _AMI_SCAN.stat().st_mtime
    result = run_alert_scan(use_lstm=use_lstm, dry_run=dry_run)
    _save_last_run(scan_mtime)
    return result


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
