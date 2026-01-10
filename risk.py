def risk_level(states):
    # Абсолютный запрет
    if states["1h"] is None:
        return "HIGH"

    risk = 0

    # конфликт таймфреймов
    if states["30m"] != states["15m"]:
        risk += 1

    # отказ против импульса
    if states["15m"] == "D" and states["30m"] == "A":
        risk += 1

    if risk == 0:
        return "LOW"
    elif risk == 1:
        return "MEDIUM"
    else:
        return "HIGH"
