"""Quản lý danh mục đầu tư: đọc CSV, nhập trực tiếp, tính lãi/lỗ, phân bổ."""
import json
import math
from pathlib import Path

import pandas as pd

from .data import get_price_board
from .config import DEFAULT_SOURCE


def fetch_sector_batch(symbols: list[str]) -> dict[str, str]:
    """Truy vấn ngành cho danh sách mã bằng Listing.symbols_by_industries() — 1 lần gọi API.
    Trả {symbol: industry_name} hoặc 'Chưa phân loại' nếu không tìm được."""
    try:
        from vnstock import Listing
        df = Listing().symbols_by_industries()
        if df is not None and not df.empty and "symbol" in df.columns and "industry_name" in df.columns:
            sym_upper = [s.upper() for s in symbols]
            mapping = df[df["symbol"].isin(sym_upper)].set_index("symbol")["industry_name"].to_dict()
            return {s: mapping.get(s.upper(), "Chưa phân loại") for s in symbols}
    except Exception:
        pass
    return {s: "Chưa phân loại" for s in symbols}


PORTFOLIO_COLUMNS = ["symbol", "quantity", "avg_price", "sector"]
_MANUAL_PATH = Path(__file__).parent.parent / "data" / "portfolio_manual.json"


def load_portfolio_manual() -> pd.DataFrame:
    """Đọc danh mục nhập tay từ JSON. Trả DataFrame rỗng nếu chưa có."""
    if _MANUAL_PATH.exists():
        try:
            rows = json.loads(_MANUAL_PATH.read_text(encoding="utf-8"))
            df = pd.DataFrame(rows)
            if df.empty:
                return _empty_portfolio()
            df.columns = [c.strip().lower() for c in df.columns]
            df["symbol"]    = df["symbol"].astype(str).str.upper().str.strip()
            df["quantity"]  = pd.to_numeric(df.get("quantity", 0), errors="coerce").fillna(0).astype(int)
            df["avg_price"] = pd.to_numeric(df.get("avg_price", 0), errors="coerce").fillna(0)
            df["sector"]    = df.get("sector", "Chưa phân loại").fillna("Chưa phân loại")
            return df[PORTFOLIO_COLUMNS]
        except Exception:
            pass
    return _empty_portfolio()


def save_portfolio_manual(df: pd.DataFrame) -> None:
    """Lưu danh mục nhập tay vào JSON."""
    _MANUAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Ép kiểu về Python native để json.dumps không lỗi với numpy int64/float64
    clean = df[PORTFOLIO_COLUMNS].copy()
    clean["symbol"]    = clean["symbol"].astype(str).str.upper().str.strip()
    clean["quantity"]  = pd.to_numeric(clean["quantity"],  errors="coerce").fillna(0).astype(int)
    clean["avg_price"] = pd.to_numeric(clean["avg_price"], errors="coerce").fillna(0.0)
    clean["sector"]    = clean["sector"].astype(str).fillna("Chưa phân loại")
    rows = [
        {
            "symbol":    str(r["symbol"]),
            "quantity":  int(r["quantity"]),
            "avg_price": float(r["avg_price"]),
            "sector":    str(r["sector"]),
        }
        for _, r in clean.iterrows()
    ]
    _MANUAL_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _empty_portfolio() -> pd.DataFrame:
    """DataFrame mẫu với 1 dòng rỗng để data_editor hiển thị."""
    return pd.DataFrame([{
        "symbol": "", "quantity": 0, "avg_price": 0.0, "sector": "Chưa phân loại"
    }])


def load_portfolio(csv_path: str) -> pd.DataFrame:
    """Đọc file CSV danh mục. Trả DataFrame rỗng nếu lỗi."""
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        df.columns = [c.strip().lower() for c in df.columns]
        for col in ["symbol", "quantity", "avg_price"]:
            if col not in df.columns:
                raise ValueError(f"Thiếu cột bắt buộc: {col}")
        df["symbol"] = df["symbol"].str.upper().str.strip()
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).astype(int)
        df["avg_price"] = pd.to_numeric(df["avg_price"], errors="coerce").fillna(0)
        if "sector" not in df.columns:
            df["sector"] = "Chưa phân loại"
        return df[PORTFOLIO_COLUMNS]
    except Exception as e:
        return pd.DataFrame(columns=PORTFOLIO_COLUMNS)


def enrich_portfolio(df: pd.DataFrame, source: str = DEFAULT_SOURCE) -> pd.DataFrame:
    """Thêm giá hiện tại, lãi/lỗ, % thay đổi phiên vào DataFrame danh mục."""
    if df.empty:
        return df

    symbols = df["symbol"].tolist()
    price_map: dict[str, float] = {}
    change_map: dict[str, float] = {}   # % thay đổi so với tham chiếu phiên
    try:
        board = get_price_board(symbols, source=source)
        if "symbol" in board.columns and "close_price" in board.columns:
            for _, row in board.iterrows():
                sym = str(row.get("symbol", "")).upper()
                price = row.get("close_price")
                if not sym:
                    continue
                if price and not (isinstance(price, float) and math.isnan(price)):
                    price_map[sym] = float(price)
                # % thay đổi phiên: (close - ref) / ref * 100
                ref = row.get("reference_price") or row.get("prior_close_price")
                if ref and not (isinstance(ref, float) and math.isnan(ref)) and float(ref) > 0:
                    close = price_map.get(sym)
                    if close:
                        change_map[sym] = round((close - float(ref)) / float(ref) * 100, 2)
    except Exception:
        pass

    df = df.copy()
    df["current_price"] = df["symbol"].map(price_map)
    df["session_change_pct"] = df["symbol"].map(change_map)
    df["market_value"] = df["current_price"] * df["quantity"]
    df["cost_value"] = df["avg_price"] * df["quantity"]
    df["pnl"] = df["market_value"] - df["cost_value"]
    df["pnl_pct"] = (df["pnl"] / df["cost_value"].replace(0, float("nan"))) * 100
    return df


def portfolio_summary(df: pd.DataFrame) -> dict:
    """Tính tổng hợp danh mục."""
    if df.empty or "market_value" not in df.columns:
        return {}
    total_cost = df["cost_value"].sum()
    total_value = df["market_value"].sum()
    total_pnl = df["pnl"].sum()
    return {
        "total_cost": total_cost,
        "total_value": total_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": (total_pnl / total_cost * 100) if total_cost else 0,
        "num_stocks": len(df),
    }


def sector_allocation(df: pd.DataFrame) -> pd.DataFrame:
    """Phân bổ danh mục theo ngành."""
    if df.empty or "market_value" not in df.columns:
        return pd.DataFrame()
    grouped = df.groupby("sector")["market_value"].sum().reset_index()
    total = grouped["market_value"].sum()
    grouped["weight_pct"] = grouped["market_value"] / total * 100
    return grouped.sort_values("weight_pct", ascending=False)
