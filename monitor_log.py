from datetime import datetime, UTC

def log_monitor(symbol, timeframe):
    with open("monitor.log", "a") as f:
        f.write(f"{datetime.now(UTC)} | {symbol} | {timeframe}\n")
