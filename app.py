import os
import asyncio
import logging
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from db import init_db
from engine import DCAEngine
from bot import build_bot

async def main():
    load_dotenv()
    api_token = os.getenv("API_TOKEN", "").strip()
    if not api_token:
        raise RuntimeError("API_TOKEN пуст. Укажи его в .env")

    await init_db()

    bot = Bot(token=api_token)
    dp = Dispatcher()

    engine = DCAEngine()
    build_bot(dp, bot, engine)

    logging.info("Polling started")
    # можно автостартовать движок: asyncio.create_task(engine.start())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
