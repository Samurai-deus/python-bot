STATE_TEXT = {
    "A": "Импульс",
    "B": "Принятие",
    "C": "Потеря контроля",
    "D": "Отказ",
    None: "Неопределённость"
}

def build_signal(symbol, states, risk, directions, zone=None):
    bias = directions.get("30m", "FLAT")

    action = "НЕ ТОРГОВАТЬ"
    if states["15m"] == "D" and bias in ["UP", "DOWN"] and risk != "HIGH":
        action = f"Приоритет: {'LONG' if bias=='UP' else 'SHORT'}"

    zone_text = ""
    if zone:
        zone_text = (
            f"\nЗона:\n"
            f"Вход: {zone['entry']}\n"
            f"Стоп: {zone['stop']}\n"
            f"Цель: {zone['target']}"
        )

    return f"""
📊 {symbol}

Контекст:
1H: {STATE_TEXT[states['1h']]} ({directions.get('1h')})
30m: {STATE_TEXT[states['30m']]} ({directions.get('30m')})
15m: {STATE_TEXT[states['15m']]}

Риск: {risk}

{action}
{zone_text}
"""
