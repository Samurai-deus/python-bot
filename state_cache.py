# хранит последнее состояние, по которому был отправлен сигнал
_last_sent = {}

def is_new_signal(symbol, state_15m):
    last = _last_sent.get(symbol)
    if last == state_15m:
        return False
    _last_sent[symbol] = state_15m
    return True
