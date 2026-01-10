import time
import subprocess
import threading

from telegram_bot import start_gpt_listener

PYTHON = "/root/market_bot/venv/bin/python"
INTERVAL = 300  # 5 минут

def run_main_loop():
    while True:
        print("🕒 Новый цикл анализа")
        subprocess.run([PYTHON, "/root/market_bot/main.py"])
        time.sleep(INTERVAL)

if __name__ == "__main__":
    # 🔥 запускаем GPT listener в фоне
    threading.Thread(
        target=start_gpt_listener,
        daemon=True
    ).start()

    # 🔁 основной цикл трейдера
    run_main_loop()
