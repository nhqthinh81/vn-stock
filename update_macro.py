"""
Cập nhật cache dữ liệu vĩ mô Việt Nam.

Chạy: python update_macro.py
Kết quả: data/macro_cache.json

Tương tự update_ranking.py nhưng cho dữ liệu kinh tế vĩ mô.
Nên chạy 1 lần/tháng (dữ liệu vĩ mô cập nhật tháng).
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from vn_invest.macro_fetcher import fetch_all, save_cache

print("🌐 Cập nhật dữ liệu vĩ mô Việt Nam")
print("Nguồn: World Bank, ExchangeRate-API, FRED, Trading Economics, EIA")
print("-" * 55)

report = fetch_all(verbose=True)
save_cache(report)

total = sum(len(g) for g in report["groups"].values())
print(f"\n✅ Hoàn thành: {total} chỉ số")
print(f"   Nguồn thành công : {', '.join(report['sources_ok']) or 'Không có'}")
print(f"   Nguồn thất bại   : {', '.join(report['sources_fail']) or 'Không có'}")
print(f"   Thời gian        : {report['generated_at'][:19]}")
print(f"\n   → data/macro_cache.json đã được cập nhật")
