import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")
from telegram import Bot
import asyncio

TOKEN = "8358679673:AAFhBTR9gumcN98cfSLOwV9OvPolAiTOqw8"
CHAT_ID = "8163327295"

bot = Bot(token=TOKEN)

async def _send(text):
    await bot.send_message(chat_id=CHAT_ID, text=text)

async def _send_chart(symbol):
    img_url = (
        "https://www.tradingview.com/x/"
        f"?symbol=BYBIT:{symbol}"
    )
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"📈 {symbol} график\n{img_url}"
    )

def send_message(text):
    asyncio.run(_send(text))

def send_chart(symbol):
    asyncio.run(_send_chart(symbol))
# ===============================
# GPT MESSAGE HANDLER (INCOMING)
# ===============================

from gpt_client import ask_gpt, GPTError
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id

    print("🔥 INCOMING MESSAGE:", text)

    # реагируем только на /gpt
    if not text.startswith("/gpt"):
        return

    prompt = text.replace("/gpt", "").strip()
    if not prompt:
        await context.bot.send_message(chat_id=chat_id, text="Напиши вопрос после /gpt")
        return

    try:
        answer = ask_gpt([
            {"role": "system", "content": "Ты помощник трейдера."},
            {"role": "user", "content": prompt}
        ])
        await context.bot.send_message(chat_id=chat_id, text=answer)

    except GPTError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ GPT error: {e}")
from telegram import Update
from telegram.ext import ContextTypes

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    print(f"📩 MESSAGE RECEIVED: {text}")

    await update.message.reply_text(
        f"🤖 Я получил сообщение:\n{text}"
    )
    except Exception as e:
        print("CRITICAL ERROR:", e)
        await context.bot.send_message(chat_id=chat_id, text="❌ Ошибка")
def start_gpt_listener():
    asyncio.run(_run())

async def _run():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
    )

    print("🤖 Telegram polling started")
    await app.run_polling()
    loop.run_until_complete(app.initialize())
    loop.run_until_complete(app.start())
    loop.run_forever()


