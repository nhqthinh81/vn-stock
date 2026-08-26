"""Quick backtest runner for optimization iterations."""
import sys
sys.path.insert(0, ".")
from vn_invest.backtester import run_backtest

for fwd in [5, 10, 20]:
    r = run_backtest(forward_days=fwd, max_symbols=200)
    s = r["summary"]
    alpha = r.get("buy_a_alpha")
    mkt   = r.get("market_avg_return")
    edge  = r.get("signal_edge")
    print(f"\n=== T+{fwd} ===")
    print(f"Alpha: {alpha:+.2f}%   MktAvg: {mkt:+.2f}%   Edge: {edge:+.2f}%")
    for sig, d in s.items():
        wr  = d["win_rate"]
        avg = d["avg_return"]
        n   = d["count"]
        std = d.get("std_return", 0)
        print(f"  {sig:8s}: wr={wr}%  avg={avg:+.2f}%  std={std:.1f}  n={n}")
