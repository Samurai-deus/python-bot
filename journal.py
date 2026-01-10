import csv
from datetime import datetime

def log_signal(symbol, states, risk):
    with open("signals_log.csv", "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.utcnow(),
            symbol,
            states["1h"],
            states["30m"],
            states["15m"],
            states["5m"],
            risk,
            "NO_ENTRY",   # сюда позже будем писать вход
            "NO_EXIT",    # выход
            "R=0"
        ])
