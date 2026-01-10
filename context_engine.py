from states import impulse, acceptance, loss_of_control, rejection

def determine_state(candles, atr_val):
    if rejection(candles, atr_val):
        return "D"
    if loss_of_control(candles):
        return "C"
    if acceptance(candles, atr_val):
        return "B"
    if impulse(candles, atr_val):
        return "A"
    return None
