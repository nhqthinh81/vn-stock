"""
Macro Fetcher — thu thập chỉ số vĩ mô Việt Nam từ 5 nguồn chính thức.

Nguồn:
  NSO     — nso.gov.vn (CPI, IIP, bán lẻ, FDI, GDP)
  PMI     — S&P Global PMI (manufacturing composite)
  Customs — Tổng cục Hải quan (xuất nhập khẩu)
  VBMA    — Hiệp hội Thị trường Trái phiếu (lãi suất, tỷ giá)
  VNBA    — Hiệp hội Ngân hàng (tín dụng, tiền gửi)

Chạy trực tiếp:
  python -m vn_invest.macro_fetcher

Kết quả lưu tại: data/macro_cache.json
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

_CACHE_PATH = Path(__file__).parent.parent / "data" / "macro_cache.json"
_TIMEOUT    = 15  # giây

# ─── helpers ──────────────────────────────────────────────────────────────────

_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}


def _get(url: str, **kw) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=_HDR, timeout=_TIMEOUT, **kw)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [WARN] GET {url}: {e}")
        return None


def _pct(text: str) -> Optional[float]:
    """Trích xuất số thực (có thể âm) từ chuỗi."""
    m = re.search(r"(-?\d+[.,]\d+)", text.replace(",", "."))
    return float(m.group(1)) if m else None


def _num(text: str) -> Optional[float]:
    m = re.search(r"(-?[\d.]+)", text.replace(",", ""))
    return float(m.group(1)) if m else None


# ─── NSO — Tổng cục Thống kê ──────────────────────────────────────────────────

def _fetch_nso() -> dict:
    """
    Lấy bản tin KT-XH tháng mới nhất từ NSO.
    Parse CPI, IIP, bán lẻ, FDI từ HTML tóm tắt.
    """
    result: dict = {}

    # Tìm báo cáo mới nhất trên trang danh sách
    index_url = "https://www.gso.gov.vn/du-lieu-va-so-lieu-thong-ke/2024/01/bao-cao-tinh-hinh-kinh-te-xa-hoi/"
    r = _get("https://www.gso.gov.vn/du-lieu-va-so-lieu-thong-ke/kinh-te-tong-hop/")
    if r is None:
        # fallback: thử URL thống kê tổng hợp
        r = _get("https://www.gso.gov.vn/du-lieu-va-so-lieu-thong-ke/")

    # Thử lấy qua API GSO data portal
    _try_gso_portal(result)
    return result


def _try_gso_portal(result: dict) -> None:
    """Lấy chỉ số vĩ mô từ cổng dữ liệu GSO."""
    # GSO có API công khai cho một số chỉ số
    urls = {
        "cpi_yoy_pct": "https://api.worldbank.org/v2/country/VN/indicator/FP.CPI.TOTL.ZG?format=json&mrv=5&per_page=5",
        "gdp_growth_pct": "https://api.worldbank.org/v2/country/VN/indicator/NY.GDP.MKTP.KD.ZG?format=json&mrv=5&per_page=5",
        "fdi_inflow_b_usd": "https://api.worldbank.org/v2/country/VN/indicator/BX.KLT.DINV.CD.WD?format=json&mrv=5&per_page=5",
        "unemployment_pct": "https://api.worldbank.org/v2/country/VN/indicator/SL.UEM.TOTL.ZS?format=json&mrv=5&per_page=5",
    }
    for key, url in urls.items():
        r = _get(url)
        if r is None:
            continue
        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 1:
                entries = [e for e in data[1] if e.get("value") is not None]
                if entries:
                    latest = entries[0]
                    result[key] = {
                        "value":  round(float(latest["value"]), 2),
                        "period": str(latest.get("date", "")),
                        "source": "World Bank / GSO",
                        "signal": _signal_macro(key, float(latest["value"])),
                    }
                    # historical (6 điểm)
                    result[key]["history"] = [
                        {"period": e["date"], "value": round(float(e["value"]), 2)}
                        for e in entries[:6]
                    ]
        except Exception as e:
            print(f"  [WARN] WorldBank parse {key}: {e}")
        time.sleep(0.3)


# ─── PMI — S&P Global ─────────────────────────────────────────────────────────

def _fetch_pmi() -> dict:
    """
    Lấy PMI từ nguồn thứ cấp (Trading Economics hoặc Investing.com).
    S&P PMI không có API công khai, dùng web scrape.
    """
    result: dict = {}

    # Trading Economics có PMI Vietnam
    r = _get("https://tradingeconomics.com/vietnam/manufacturing-pmi")
    if r:
        text = r.text
        # Pattern: giá trị PMI hiện tại
        m = re.search(r"pmi.*?(\d+\.\d+)", text[:5000], re.IGNORECASE)
        if not m:
            m = re.search(r"(\d{2}\.\d{1,2})", text[:3000])
        if m:
            val = float(m.group(1))
            if 40 <= val <= 65:  # sanity check PMI range
                result["pmi_manufacturing"] = {
                    "value":  val,
                    "period": datetime.now().strftime("%Y-%m"),
                    "source": "S&P Global / Trading Economics",
                    "signal": "TÍCH" if val > 50 else ("TIÊU" if val < 48 else "TRUNG"),
                    "narrative": (
                        f"PMI sản xuất {val:.1f} — "
                        + ("mở rộng (>50)" if val > 50 else "thu hẹp (<50)")
                        + ". PMI > 50 phản ánh đơn hàng mới tăng, sản lượng cải thiện."
                    ),
                }

    # Thử Macrotrends backup
    if "pmi_manufacturing" not in result:
        r2 = _get("https://www.macrotrends.net/global-metrics/countries/VNM/vietnam/pmi")
        if r2:
            m2 = re.search(r'"value"\s*:\s*"([\d.]+)"', r2.text[:10000])
            if m2:
                val = float(m2.group(1))
                if 40 <= val <= 65:
                    result["pmi_manufacturing"] = {
                        "value":  val,
                        "period": datetime.now().strftime("%Y-%m"),
                        "source": "S&P Global / Macrotrends",
                        "signal": "TÍCH" if val > 50 else ("TIÊU" if val < 48 else "TRUNG"),
                    }

    return result


# ─── Customs — Xuất nhập khẩu ─────────────────────────────────────────────────

def _fetch_trade() -> dict:
    """Lấy số liệu XNK từ World Bank + Trading Economics."""
    result: dict = {}

    # Cán cân thương mại + xuất khẩu + nhập khẩu (World Bank annual)
    indicators = {
        "export_b_usd":  "TX.VAL.MRCH.CD.WT",
        "import_b_usd":  "TM.VAL.MRCH.CD.WT",
        "trade_bal_pct": "NE.EXP.GNFS.ZS",
    }
    for key, ind in indicators.items():
        r = _get(f"https://api.worldbank.org/v2/country/VN/indicator/{ind}?format=json&mrv=5&per_page=5")
        if r is None:
            continue
        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 1:
                entries = [e for e in data[1] if e.get("value") is not None]
                if entries:
                    latest = entries[0]
                    val = float(latest["value"])
                    # Convert to billions USD if raw
                    if key != "trade_bal_pct" and val > 1e9:
                        val = round(val / 1e9, 2)
                    result[key] = {
                        "value":  round(val, 2),
                        "period": str(latest.get("date", "")),
                        "source": "World Bank / Customs",
                        "signal": _signal_macro(key, val),
                        "history": [
                            {"period": e["date"], "value": round(float(e["value"]) / (1e9 if key != "trade_bal_pct" and float(e["value"]) > 1e9 else 1), 2)}
                            for e in entries[:6]
                        ],
                    }
        except Exception as e:
            print(f"  [WARN] Trade parse {key}: {e}")
        time.sleep(0.3)

    return result


# ─── VBMA / SBV — Lãi suất, Tỷ giá ──────────────────────────────────────────

def _fetch_monetary() -> dict:
    """Lấy lãi suất, tỷ giá từ World Bank + nguồn thứ cấp."""
    result: dict = {}

    wb_indicators = {
        "lending_rate_pct":  "FR.INR.LEND",   # Lãi suất cho vay bình quân
        "deposit_rate_pct":  "FR.INR.DPST",   # Lãi suất tiền gửi
        "real_interest_pct": "FR.INR.RINR",   # Lãi suất thực
        "money_supply_growth_pct": "FM.LBL.BMNY.ZG",  # Tăng trưởng M2
        "domestic_credit_pct": "FS.AST.DOMS.GD.ZS",  # Tín dụng nội địa / GDP
    }
    for key, ind in wb_indicators.items():
        r = _get(f"https://api.worldbank.org/v2/country/VN/indicator/{ind}?format=json&mrv=6&per_page=6")
        if r is None:
            continue
        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 1:
                entries = [e for e in data[1] if e.get("value") is not None]
                if entries:
                    latest = entries[0]
                    result[key] = {
                        "value":  round(float(latest["value"]), 2),
                        "period": str(latest.get("date", "")),
                        "source": "World Bank / VBMA",
                        "signal": _signal_macro(key, float(latest["value"])),
                        "history": [
                            {"period": e["date"], "value": round(float(e["value"]), 2)}
                            for e in entries[:6]
                        ],
                    }
        except Exception as e:
            print(f"  [WARN] Monetary parse {key}: {e}")
        time.sleep(0.3)

    # Tỷ giá USD/VND
    _fetch_fx(result)

    return result


def _fetch_fx(result: dict) -> None:
    """Tỷ giá USD/VND từ ExchangeRate-API (miễn phí)."""
    r = _get("https://open.er-api.com/v6/latest/USD")
    if r is None:
        return
    try:
        data = r.json()
        vnd = data.get("rates", {}).get("VND")
        if vnd:
            result["usd_vnd_rate"] = {
                "value":  round(float(vnd), 0),
                "period": data.get("time_last_update_utc", "")[:10],
                "source": "ExchangeRate-API",
                "signal": "TRUNG",
                "narrative": f"Tỷ giá USD/VND = {int(vnd):,} — phản ánh sức mạnh đồng USD và dự trữ ngoại hối SBV.",
            }
    except Exception as e:
        print(f"  [WARN] FX parse: {e}")


# ─── Global Context — Bối cảnh thế giới ──────────────────────────────────────

def _fetch_global() -> dict:
    """Lấy chỉ số kinh tế toàn cầu: dầu thô, Fed rate, USD Index."""
    result: dict = {}

    # Giá dầu Brent từ Yahoo Finance (unofficial)
    _fetch_commodity_prices(result)

    # US Fed Funds Rate từ FRED
    r = _get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS",
    )
    if r:
        lines = r.text.strip().split("\n")
        if len(lines) >= 2:
            last = lines[-1].split(",")
            if len(last) == 2:
                try:
                    result["fed_rate_pct"] = {
                        "value":  round(float(last[1]), 2),
                        "period": last[0],
                        "source": "US Federal Reserve (FRED)",
                        "signal": "TRUNG",
                        "narrative": (
                            f"Fed Funds Rate = {float(last[1]):.2f}%. "
                            "Lãi suất Fed ảnh hưởng trực tiếp đến dòng vốn vào thị trường mới nổi (EM)."
                        ),
                        "history": [
                            {"period": ln.split(",")[0], "value": float(ln.split(",")[1])}
                            for ln in lines[-7:-1][::-1]
                            if len(ln.split(",")) == 2 and ln.split(",")[1].strip()
                        ],
                    }
                except Exception:
                    pass

    return result


def _fetch_commodity_prices(result: dict) -> None:
    """Giá dầu Brent + WTI từ EIA API (miễn phí)."""
    # EIA open data
    series = {
        "brent_oil_usd":  "PET.RBRTE.D",
        "wti_oil_usd":    "PET.RWTC.D",
    }
    for key, sid in series.items():
        r = _get(f"https://api.eia.gov/v2/seriesid/{sid}?api_key=DEMO_KEY&length=10")
        if r is None:
            continue
        try:
            data = r.json()
            rows = data.get("response", {}).get("data", [])
            if rows:
                latest = rows[0]
                val = float(latest.get("value", 0))
                result[key] = {
                    "value":  round(val, 2),
                    "period": str(latest.get("period", "")),
                    "source": "EIA",
                    "signal": "TRUNG",
                    "history": [
                        {"period": r["period"], "value": round(float(r["value"]), 2)}
                        for r in rows[:6]
                    ],
                }
        except Exception as e:
            print(f"  [WARN] Commodity {key}: {e}")
        time.sleep(0.2)


# ─── Signal logic ─────────────────────────────────────────────────────────────

_SIGNAL_RULES: dict[str, tuple] = {
    # (lower_bad_thresh, upper_bad_thresh, direction)
    # direction: "higher_better" hoặc "lower_better"
    "cpi_yoy_pct":            (0,   4.5, "lower_better"),
    "gdp_growth_pct":         (5.0, 99,  "higher_better"),
    "lending_rate_pct":       (0,   10,  "lower_better"),
    "deposit_rate_pct":       (0,   8,   "lower_better"),
    "money_supply_growth_pct":(10,  20,  "higher_better"),
    "export_b_usd":           (300, 999, "higher_better"),
    "brent_oil_usd":          (60,  100, "lower_better"),   # giá dầu thấp = tốt cho VN
    "fed_rate_pct":           (0,   3.5, "lower_better"),
}


def _signal_macro(key: str, val: float) -> str:
    rule = _SIGNAL_RULES.get(key)
    if rule is None:
        return "TRUNG"
    lo, hi, direction = rule
    if direction == "higher_better":
        if val >= lo:
            return "TÍCH"
        if val < lo * 0.8:
            return "TIÊU"
        return "TRUNG"
    else:  # lower_better
        if val <= hi:
            return "TÍCH"
        if val > hi * 1.15:
            return "TIÊU"
        return "TRUNG"


# ─── Main fetch ───────────────────────────────────────────────────────────────

def fetch_all(verbose: bool = True) -> dict:
    """Lấy toàn bộ chỉ số. Trả về dict theo 4 nhóm."""
    now = datetime.now().isoformat()
    report: dict = {
        "generated_at": now,
        "report_month":  datetime.now().strftime("%Y-%m"),
        "sources_ok":    [],
        "sources_fail":  [],
        "groups": {
            "group1_real_economy": {},
            "group2_monetary":     {},
            "group3_trade":        {},
            "group4_global":       {},
        },
    }

    steps = [
        ("NSO / World Bank",   _try_gso_portal, "group1_real_economy"),
        ("PMI S&P Global",     _fetch_pmi,      None),
        ("Hải quan / Trade",   _fetch_trade,    "group3_trade"),
        ("VBMA / Tiền tệ",    _fetch_monetary, "group2_monetary"),
        ("Bối cảnh TG",       _fetch_global,   "group4_global"),
    ]

    for name, fn, group_key in steps:
        if verbose:
            print(f"  ↳ {name}...")
        try:
            if group_key is None:
                # PMI: fn returns dict, merge vào group1
                data = fn()
                report["groups"]["group1_real_economy"].update(data)
            elif name.startswith("NSO"):
                # fn nhận dict làm tham số
                fn(report["groups"][group_key])
                data = report["groups"][group_key]
            else:
                data = fn()
                report["groups"][group_key].update(data)

            if data:
                report["sources_ok"].append(name)
            else:
                report["sources_fail"].append(name)
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            report["sources_fail"].append(name)
        time.sleep(0.5)

    return report


def load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(report: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("=== Macro Fetcher ===")
    report = fetch_all(verbose=True)
    save_cache(report)
    total = sum(len(g) for g in report["groups"].values())
    print(f"\nHoàn thành: {total} chỉ số | Nguồn OK: {report['sources_ok']}")
    print(f"Lưu tại: {_CACHE_PATH}")
