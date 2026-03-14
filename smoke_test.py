"""
Testnet Smoke Test — запускать вручную перед Phase 5.

Проверяет:
1. API connectivity (публичный endpoint)
2. Аутентификацию (wallet balance)
3. qtyStep для BTC
4. Постановку и немедленную отмену минимального ордера на Testnet

Запуск:
    cd market_bot
    source venv/bin/activate   # или venv\Scripts\activate на Windows
    python smoke_test.py
"""
import os
import sys
import time

# Подтягиваем .env до импорта клиента
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv опционально, можно использовать уже экспортированные переменные


def check(label: str, ok: bool, detail: str = "") -> None:
    icon = "✅" if ok else "❌"
    msg = f"{icon}  {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return ok


def main() -> int:
    print("\n=== Bybit Testnet Smoke Test ===\n")

    testnet = os.environ.get("BYBIT_TESTNET", "false").lower() == "true"
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")

    # --- Step 0: env sanity ---
    print("── Env ──────────────────────────────────────────────")
    check("BYBIT_TESTNET=true", testnet, f"current: {os.environ.get('BYBIT_TESTNET')}")
    check("DRY_RUN=false", not dry_run, f"current: {os.environ.get('DRY_RUN')}")
    check("BYBIT_API_KEY set", bool(api_key), f"len={len(api_key)}")
    check("BYBIT_API_SECRET set", bool(api_secret), f"len={len(api_secret)}")

    if not testnet:
        print("\n⚠️  BYBIT_TESTNET не true — выходим (не хотим рисковать реальными деньгами)")
        return 1
    if not api_key or not api_secret:
        print("\n⚠️  API ключи не найдены в .env — выходим")
        return 1

    from exchange.bybit_client import BybitClient, BybitAPIError

    client = BybitClient()
    errors = 0

    # --- Step 1: публичный endpoint (kline) ---
    print("\n── Step 1: Public API (klines) ──────────────────────")
    try:
        candles = client.get_klines("BTCUSDT", "1", limit=3)
        ok = len(candles) > 0
        check("GET /v5/market/kline", ok, f"got {len(candles)} candles")
        if not ok:
            errors += 1
    except Exception as e:
        check("GET /v5/market/kline", False, str(e))
        errors += 1

    # --- Step 2: instruments-info + qtyStep ---
    print("\n── Step 2: instruments-info / qtyStep ───────────────")
    try:
        step = client.get_qty_step("BTCUSDT")
        check("get_qty_step(BTCUSDT)", step > 0, f"qtyStep={step}")
    except Exception as e:
        check("get_qty_step(BTCUSDT)", False, str(e))
        errors += 1

    # --- Step 3: аутентификация (wallet balance) ---
    print("\n── Step 3: Auth — wallet balance ────────────────────")
    balance = None
    try:
        balance = client.get_wallet_balance("USDT")
        ok = balance.total_equity >= 0
        check(
            "GET /v5/account/wallet-balance",
            ok,
            f"equity={balance.total_equity:.2f} USDT, "
            f"available={balance.available_balance:.2f} USDT",
        )
        if balance.available_balance < 1:
            print("  ⚠️  Доступный баланс < 1 USDT — пополни Testnet кошелёк на https://testnet.bybit.com/faucet")
            errors += 1
    except BybitAPIError as e:
        check("GET /v5/account/wallet-balance", False, str(e))
        errors += 1
    except Exception as e:
        check("GET /v5/account/wallet-balance", False, f"{type(e).__name__}: {e}")
        errors += 1

    # --- Step 4: тестовый ордер (place + cancel) ---
    print("\n── Step 4: Place + Cancel test order ───────────────")
    if balance is None or balance.available_balance < 1:
        print("  ⏭  Пропуск — недостаточный баланс на Testnet")
    else:
        # Минимальный BTCUSDT ордер: используем qtyStep как qty
        try:
            step = client.get_qty_step("BTCUSDT")
            test_qty = step  # минимально возможный размер
            # Ставим limit far-from-market чтобы не исполнился случайно
            candles = client.get_klines("BTCUSDT", "1", limit=1)
            current_price = float(candles[-1][4]) if candles else 30000.0
            limit_price = round(current_price * 0.50, 1)  # -50% от рынка → не исполнится

            result = client.place_order(
                symbol="BTCUSDT",
                side="Buy",
                qty=test_qty,
                order_type="Limit",
                price=limit_price,
            )
            placed_ok = bool(result.order_id)
            check(
                "place_order (Limit Buy BTCUSDT)",
                placed_ok,
                f"order_id={result.order_id}, qty={test_qty}, price={limit_price}",
            )

            if placed_ok:
                time.sleep(1)
                cancelled = client.cancel_order("BTCUSDT", result.order_id)
                check("cancel_order", cancelled, f"order_id={result.order_id}")
                if not cancelled:
                    errors += 1
            else:
                errors += 1

        except BybitAPIError as e:
            check("place_order", False, str(e))
            errors += 1
        except Exception as e:
            check("place_order", False, f"{type(e).__name__}: {e}")
            errors += 1

    # --- Summary ---
    print("\n── Summary ──────────────────────────────────────────")
    if errors == 0:
        print("✅  Все проверки пройдены — Testnet готов к работе!\n")
        return 0
    else:
        print(f"❌  Ошибок: {errors} — исправь их до запуска бота\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
