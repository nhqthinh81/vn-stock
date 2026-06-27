"""
Paper Trading Tracker — ghi nhận BUY-A signal, theo dõi P&L theo thời gian thực.

Data model (mỗi trade trong paper_trades.json):
{
  "id":         "HPG_20260627",
  "symbol":     "HPG",
  "entry_date": "2026-06-27",
  "entry_price": 28.5,
  "tech_score": 72.0,
  "rsi":        45.2,
  "signal":     "BUY-A",
  "status":     "open" | "closed",
  "exit_date":  null | "2026-08-01",
  "exit_price": null | 31.0,
  "return_pct": null | 8.77,
  "result":     null | "win" | "loss",
  "t5_target":  "2026-08-01",   # T+5 tuần
  "note":       ""
}
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_DATA_PATH = Path(__file__).parent.parent / "data" / "paper_trades.json"
_T5_WEEKS  = 5   # khung đánh giá mặc định: 5 tuần


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _add_weeks(date_str: str, weeks: int) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(weeks=weeks)
    return d.strftime("%Y-%m-%d")


def load_trades() -> list[dict]:
    if not _DATA_PATH.exists():
        return []
    try:
        return json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_trades(trades: list[dict]) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DATA_PATH.write_text(json.dumps(trades, ensure_ascii=False, indent=2), encoding="utf-8")


def add_trade(symbol: str, entry_price: float,
              tech_score: float = 0.0, rsi: float = 0.0,
              signal: str = "BUY-A", note: str = "") -> dict:
    """Thêm trade mới. Trả về trade đã thêm."""
    trades = load_trades()
    today  = _today()
    trade_id = f"{symbol}_{today}"

    # Tránh duplicate cùng ngày
    existing = [t for t in trades if t["id"] == trade_id]
    if existing:
        return existing[0]

    trade = {
        "id":          trade_id,
        "symbol":      symbol.upper(),
        "entry_date":  today,
        "entry_price": round(float(entry_price), 2),
        "tech_score":  round(float(tech_score), 1),
        "rsi":         round(float(rsi), 1),
        "signal":      signal,
        "status":      "open",
        "exit_date":   None,
        "exit_price":  None,
        "return_pct":  None,
        "result":      None,
        "t5_target":   _add_weeks(today, _T5_WEEKS),
        "note":        note,
    }
    trades.append(trade)
    save_trades(trades)
    return trade


def close_trade(trade_id: str, exit_price: float,
                exit_date: Optional[str] = None) -> Optional[dict]:
    """Đóng trade thủ công với giá exit."""
    trades = load_trades()
    for t in trades:
        if t["id"] == trade_id and t["status"] == "open":
            t["status"]     = "closed"
            t["exit_date"]  = exit_date or _today()
            t["exit_price"] = round(float(exit_price), 2)
            t["return_pct"] = round((exit_price - t["entry_price"]) / t["entry_price"] * 100, 2)
            t["result"]     = "win" if t["return_pct"] > 0 else "loss"
            save_trades(trades)
            return t
    return None


def delete_trade(trade_id: str) -> bool:
    trades = load_trades()
    before = len(trades)
    trades = [t for t in trades if t["id"] != trade_id]
    if len(trades) < before:
        save_trades(trades)
        return True
    return False


def update_prices(price_map: dict[str, float]) -> list[dict]:
    """Cập nhật giá hiện tại cho trades đang mở. Trả list trades đã update."""
    trades = load_trades()
    today  = _today()
    changed = False

    for t in trades:
        if t["status"] != "open":
            continue
        sym = t["symbol"]
        cur_price = price_map.get(sym)
        if cur_price and cur_price > 0:
            t["current_price"] = round(float(cur_price), 2)
            t["unrealized_pct"] = round((cur_price - t["entry_price"]) / t["entry_price"] * 100, 2)
            changed = True

        # Auto-close khi quá T+5 tuần: dùng current_price nếu có
        if t.get("t5_target") and today >= t["t5_target"]:
            close_px = t.get("current_price") or t["entry_price"]
            t["status"]     = "closed"
            t["exit_date"]  = today
            t["exit_price"] = close_px
            t["return_pct"] = round((close_px - t["entry_price"]) / t["entry_price"] * 100, 2)
            t["result"]     = "win" if t["return_pct"] > 0 else "loss"
            changed = True

    if changed:
        save_trades(trades)
    return trades


def get_stats(trades: Optional[list[dict]] = None) -> dict:
    """Tính win rate và các chỉ số tổng hợp."""
    if trades is None:
        trades = load_trades()

    closed = [t for t in trades if t["status"] == "closed" and t.get("return_pct") is not None]
    open_  = [t for t in trades if t["status"] == "open"]

    if not closed:
        return {"total_closed": 0, "win": 0, "loss": 0, "win_rate": None,
                "avg_return": None, "best": None, "worst": None, "total_open": len(open_)}

    wins   = [t for t in closed if t["result"] == "win"]
    losses = [t for t in closed if t["result"] == "loss"]
    returns = [t["return_pct"] for t in closed]

    return {
        "total_closed": len(closed),
        "total_open":   len(open_),
        "win":          len(wins),
        "loss":         len(losses),
        "win_rate":     round(len(wins) / len(closed) * 100, 1),
        "avg_return":   round(sum(returns) / len(returns), 2),
        "best":         round(max(returns), 2),
        "worst":        round(min(returns), 2),
    }
