import os
import requests
import traceback
import logging

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "gpt-4o-mini"

HEADERS = {
    "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}",
    "Content-Type": "application/json",
    # обязательные заголовки OpenRouter
    "HTTP-Referer": "https://example.com",
    "X-Title": "market_bot",
}

TIMEOUT = 30


class GPTError(Exception):
    pass


def ask_gpt(messages: list[str]) -> str:
    """
    messages: список строк (user messages)
    return: текст ответа GPT
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise GPTError("OPENROUTER_API_KEY не задан")

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": m} for m in messages],
    }

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers=HEADERS,
            json=payload,
            timeout=TIMEOUT,
        )

        # --- HTTP ошибки ---
        if resp.status_code == 401:
            raise GPTError("❌ Ошибка авторизации OpenRouter (401)")

        if resp.status_code == 429:
            raise GPTError("⚠️ Лимит запросов GPT временно превышен")

        if resp.status_code >= 500:
            raise GPTError("⚠️ GPT временно недоступен (5xx)")

        data = resp.json()

        if "choices" not in data:
            raise GPTError(f"Неожиданный ответ GPT: {data}")

        return data["choices"][0]["message"]["content"]

    except requests.Timeout:
        raise GPTError("⏳ Таймаут при обращении к GPT")

    except Exception as e:
        logging.error(traceback.format_exc())
        raise GPTError("❌ Внутренняя ошибка GPT")
