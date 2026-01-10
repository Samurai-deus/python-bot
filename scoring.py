def calculate_score(states, directions, is_flat, good_time):
    score = 0
    reasons = []

    # 1H и 30m в одном направлении
    if directions.get("1h") == directions.get("30m") and directions.get("30m") in ["UP", "DOWN"]:
        score += 25
        reasons.append("1H и 30m в одном направлении")

    # Отказ на 15m
    if states.get("15m") == "D":
        score += 25
        reasons.append("Чёткий отказ на 15m")

    # Не флэт
    if not is_flat:
        score += 25
        reasons.append("Рынок не во флэте")

    # Торговое время
    if good_time:
        score += 25
        reasons.append("Торговое время")

    return score, reasons


def market_mode(score):
    if score >= 75:
        return "TRADE"
    elif score >= 50:
        return "OBSERVE"
    else:
        return "STOP"
