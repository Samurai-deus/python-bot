from datetime import datetime

def log_monitor(symbol, timeframe):
    with open("monitor.log", "a") as f:
        f.write(f"{datetime.utcnow()} | {symbol} | {timeframe}\n")
