"""CLI đơn giản để scan + xem tín hiệu từ terminal."""
import argparse
import sys

from .screener import scan_symbol, scan_watchlist, load_cache, filter_cache
from .config import DEFAULT_WATCHLIST


def cmd_scan(args):
    if args.symbol:
        symbols = [s.upper() for s in args.symbol]
        print(f"Đang scan {len(symbols)} mã...")
        for sym in symbols:
            rec = scan_symbol(sym)
            if rec:
                print(f"\n{'='*40}")
                print(f"  {rec['symbol']}")
                print(f"  Giá:       {rec['close']:,.0f}")
                print(f"  RSI:       {rec['rsi']:.1f}")
                print(f"  Tín hiệu:  {rec['signal']}")
                print(f"  Rủi ro:    {rec['risk']}")
                print(f"  Giai đoạn: {rec['phase']}")
                print(f"  Điểm KT:   {rec['tech_score']:.1f}")
            else:
                print(f"  {sym}: Không lấy được dữ liệu")
    else:
        print(f"Scan watchlist ({len(DEFAULT_WATCHLIST)} mã)...")
        results = scan_watchlist(progress_callback=lambda i, t, s: print(f"  [{i+1}/{t}] {s}"))
        print(f"\nHoàn tất: {len(results)} mã")


def cmd_list(args):
    data = filter_cache(signal=args.signal, risk=args.risk)
    if not data:
        print("Cache rỗng. Chạy: python -m vn_invest scan")
        return
    print(f"\n{'Mã':<8} {'Giá':>10} {'RSI':>6} {'Tín hiệu':<10} {'Rủi ro':<8} {'Giai đoạn':<14} {'Điểm KT':>8}")
    print("-" * 70)
    for r in data:
        print(
            f"{r['symbol']:<8} {r.get('close', 0):>10,.0f} {r.get('rsi', 0):>6.1f} "
            f"{r.get('signal', '')::<10} {r.get('risk', '')::<8} "
            f"{r.get('phase', '')::<14} {r.get('tech_score', 0):>8.1f}"
        )


def main():
    parser = argparse.ArgumentParser(description="vn-invest CLI — Phân tích chứng khoán Việt Nam")
    sub = parser.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="Scan mã hoặc toàn watchlist")
    p_scan.add_argument("symbol", nargs="*", help="Mã cổ phiếu (bỏ trống = scan watchlist)")
    p_scan.set_defaults(func=cmd_scan)

    p_list = sub.add_parser("list", help="Xem danh sách từ cache")
    p_list.add_argument("--signal", choices=["BUY-A", "BUY-B", "HOLD", "SELL-B", "SELL-A"])
    p_list.add_argument("--risk", choices=["Low", "Medium", "High"])
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
