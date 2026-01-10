import csv
from datetime import datetime

def log_demo_trade(symbol, side, entry, stop, target):
    with open("demo_trades.csv", "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.utcnow(),
            symbol,
            side,
            entry,
            stop,
            target,
            "OPEN"
        ])
