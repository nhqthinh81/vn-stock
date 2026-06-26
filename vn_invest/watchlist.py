"""Watchlist — lưu danh sách mã quan tâm vào JSON local."""
import json
from datetime import datetime
from pathlib import Path

_WATCHLIST_PATH = Path(__file__).parent.parent / "data" / "watchlist.json"


def load_watchlist() -> list[str]:
    if _WATCHLIST_PATH.exists():
        try:
            data = json.loads(_WATCHLIST_PATH.read_text(encoding="utf-8"))
            return [s.upper() for s in data.get("symbols", [])]
        except Exception:
            pass
    return []


def save_watchlist(symbols: list[str]) -> None:
    _WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WATCHLIST_PATH.write_text(
        json.dumps({
            "symbols":    [s.upper() for s in symbols],
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_to_watchlist(symbol: str) -> bool:
    """Trả True nếu thêm thành công (không trùng)."""
    symbol = symbol.upper().strip()
    symbols = load_watchlist()
    if symbol in symbols:
        return False
    symbols.append(symbol)
    save_watchlist(symbols)
    return True


def remove_from_watchlist(symbol: str) -> bool:
    """Trả True nếu xóa thành công."""
    symbol = symbol.upper().strip()
    symbols = load_watchlist()
    if symbol not in symbols:
        return False
    symbols.remove(symbol)
    save_watchlist(symbols)
    return True
