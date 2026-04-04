#!/usr/bin/env python3
"""Diagnostic: trace PnL data from DB through performance_tracker to API response."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
from analytics.performance_tracker import PerformanceTracker

print("=== get_closed_trades ===")
for days in [1, 7, 30, 365]:
    trades = db.get_closed_trades(days)
    pnl = sum(t["net_pnl"] for t in trades)
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    print(f"  {days}d: {len(trades)} trades, {wins} wins, pnl=${pnl:.2f}")

print("\n=== PerformanceTracker.get_full_report ===")
tracker = PerformanceTracker()
for days in [1, 7, 30]:
    r = tracker.get_full_report(days)
    if r:
        print(f"  {days}d: trades={r.total_trades} wr={r.win_rate} pnl={r.total_net_pnl} dd={r.max_drawdown_pct} pf={r.profit_factor} sr={r.sharpe_ratio}")
        print(f"       equity_curve: {len(r.equity_curve)} pts", end="")
        if r.equity_curve:
            print(f" [{r.equity_curve[0]:.2f} ... {r.equity_curve[-1]:.2f}]")
        else:
            print(" (empty)")
    else:
        print(f"  {days}d: None")

print("\n=== get_monthly_target_data ===")
d = db.get_monthly_target_data()
for k, v in d.items():
    print(f"  {k}: {v}")

print("\n=== get_equity_curve_points ===")
eq = db.get_equity_curve_points(30)
print(f"  {len(eq)} points")
if eq:
    print(f"  first: {eq[0]}")
    print(f"  last: {eq[-1]}")

print("\n=== by_symbol (top 5 by trades) ===")
by_sym = tracker.get_pnl_by_symbol(30)
sorted_syms = sorted(by_sym.values(), key=lambda x: x.trades, reverse=True)[:5]
for s in sorted_syms:
    print(f"  {s.symbol}: trades={s.trades} wins={s.wins} wr={s.win_rate:.1f}% pnl=${s.net_pnl:.2f}")
