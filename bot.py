import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from db import init_db, fetch_white_list_pairs, fetch_trade_params
from engine import DCAEngine

# Клавиатуры
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Запустить"), KeyboardButton(text="🛑 Остановить")],
        [KeyboardButton(text="⏸️ Пауза"), KeyboardButton(text="🔄 Перезапустить")],
        [KeyboardButton(text="📂 Открытые позиции"), KeyboardButton(text="✅ Whitelist")],
        [KeyboardButton(text="⚙️ Торговые параметры")]
    ],
    resize_keyboard=True
)

MENU_TEXTS = {btn.text for row in main_menu.keyboard for btn in row}

def build_bot(dp: Dispatcher, bot: Bot, engine: DCAEngine):
    async def start_cmd(message: types.Message):
        await message.answer("Выберите действие:", reply_markup=main_menu)

    async def main_handler(message: types.Message):
        txt = message.text

        if txt == "🟢 Запустить":
            await engine.start(lambda m: message.answer(m))
            await message.answer("▶️ Движок запущен.", reply_markup=main_menu); return

        if txt == "⏸️ Пауза":
            await engine.pause()
            await message.answer("⏸️ Пауза DCA.", reply_markup=main_menu); return

        if txt == "🔄 Перезапустить":
            await engine.stop()
            await engine.start(lambda m: message.answer(m))
            await message.answer("♻️ Перезапуск движка.", reply_markup=main_menu); return

        if txt == "🛑 Остановить":
            await engine.stop()
            await message.answer("⛔ Остановлено.", reply_markup=main_menu); return

        if txt == "✅ Whitelist":
            pairs = await fetch_white_list_pairs()
            text = "Список пуст." if not pairs else "Whitelist ("+str(len(pairs))+"):\n" + "\n".join(pairs)
            await message.answer(text, reply_markup=main_menu); return

        if txt == "⚙️ Торговые параметры":
            tp = await fetch_trade_params()
            if not tp:
                await message.answer("trade_params пусто.", reply_markup=main_menu); return
            lines = [f"{k}: {v}" for k, v in tp.items() if k != "id"]
            await message.answer("Текущие параметры:\n" + "\n".join(lines), reply_markup=main_menu); return

        if txt == "📂 Открытые позиции":
            # быстрый просмотр из БД (после reconcile движка данные будут актуальны)
            from aiosqlite import connect
            async with connect("bot.db") as db:
                db.row_factory = lambda c, r: {"pair": r[0], "qty": r[1], "avg": r[2]}
                async with db.execute("SELECT pair, freeQuantity, averagePrice FROM symbols WHERE statusOrder='OPEN'") as cur:
                    rows = await cur.fetchall()
            if not rows:
                await message.answer("Нет открытых позиций.", reply_markup=main_menu)
            else:
                out = "\n".join([f"{r['pair']}: qty={r['qty']} avg={r['avg']}" for r in rows])
                await message.answer(out, reply_markup=main_menu)
            return

        await message.answer("Неизвестная команда.", reply_markup=main_menu)

    dp.message.register(start_cmd, CommandStart())
    dp.message.register(start_cmd, Command("menu"))
    dp.message.register(main_handler, F.text.in_(MENU_TEXTS))
