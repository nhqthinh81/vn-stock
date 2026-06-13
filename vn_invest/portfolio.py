"""Quản lý danh mục đầu tư: đọc CSV, tính lãi/lỗ, phân bổ."""
import math
import pandas as pd

from .data import get_price_board
from .config import DEFAULT_SOURCE


PORTFOLIO_COLUMNS = ["symbol", "quantity", "avg_price", "sector"]


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
    """Thêm giá hiện tại, lãi/lỗ, % thay đổi vào DataFrame danh mục."""
    if df.empty:
        return df

    symbols = df["symbol"].tolist()
    price_map = {}
    try:
        board = get_price_board(symbols, source=source)
        if "symbol" in board.columns and "close_price" in board.columns:
            for _, row in board.iterrows():
                sym = str(row.get("symbol", "")).upper()
                price = row.get("close_price")
                if sym and price and not (isinstance(price, float) and math.isnan(price)):
                    price_map[sym] = float(price)
    except Exception:
        pass

    df = df.copy()
    df["current_price"] = df["symbol"].map(price_map)
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
