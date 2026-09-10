"""
Проверка авторизации на ЖИВОМ API — изнутри контейнера API после деплоя.

Подписывает initData настоящим токеном бота (он есть в окружении контейнера) и
проверяет три исхода на /api/system/health:
    без initData          → 401
    чужой пользователь    → 403   (раньше подписи хватало, чтобы видеть данные)
    владелец              → 200

Проверка снаружи curl-ом умеет только первое: подписать initData без токена
нельзя. А главное, что нужно доказать на проде, — второе и третье: что
боевая конфигурация действительно пускает владельца и не пускает остальных.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

BASE = os.environ.get("SMOKE_API_BASE", "http://localhost:8000")


def init_data(user_id: int, bot_token: str) -> str:
    params = {
        "auth_date": str(int(time.time())),
        "query_id": "smoke",
        "user": json.dumps({"id": user_id, "first_name": "smoke"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())


def status(headers: dict) -> int:
    request = urllib.request.Request(BASE + "/api/system/health", headers=headers)
    try:
        return urllib.request.urlopen(request, timeout=10).status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner = os.environ.get("ADMIN_CHAT_ID", "")
    if not token or not owner.isdigit():
        print("  нет TELEGRAM_BOT_TOKEN или ADMIN_CHAT_ID в окружении", file=sys.stderr)
        return 1
    owner_id = int(owner)

    checks = [
        ("без initData", {}, 401),
        ("чужой пользователь", {"X-Telegram-Init-Data": init_data(owner_id + 1, token)}, 403),
        ("владелец", {"X-Telegram-Init-Data": init_data(owner_id, token)}, 200),
    ]
    ok = True
    for name, headers, expected in checks:
        got = status(headers)
        mark = "ok" if got == expected else "ОШИБКА"
        print(f"  {mark}  {name}: {got} (ждали {expected})")
        ok = ok and got == expected
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
