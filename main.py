import os
import asyncio
import logging
from textwrap import wrap
import re

from aiogram import Bot, Dispatcher, types, F

from aiogram.filters import Command, CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
import aiosqlite

DB_PATH = os.getenv("DB_PATH", "bot.db")  # можно переопределить через .env
# ---------- CONFIG ----------
load_dotenv()
API_TOKEN = "8158003723:AAHr4WbpUYUKGLw_B2VoeiwAntYkZrxdMis"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

# --------------------------
# KEYBOARDS
# --------------------------
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛑 Остановить A-Bot"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔄 Перезагрузить A-Bot"), KeyboardButton(text="📂 Открытые позиции")],
        [KeyboardButton(text="⏸️ Приостановить торги"), KeyboardButton(text="⚙️ Торговые параметры")],
        [KeyboardButton(text="🇬🇧 English"), KeyboardButton(text="✅ Whitelist")]
    ],
    resize_keyboard=True
)

whitelist_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⬅️ В главное меню")],
        [KeyboardButton(text="✅ Добавить"), KeyboardButton(text="❌ Удалить")],
        [KeyboardButton(text="✅ Добавить все монеты"), KeyboardButton(text="🔁 Вернуть список по умолчанию")]
    ],
    resize_keyboard=True
)

MENU_TEXTS = {
    "🛑 Остановить A-Bot",
    "📊 Статистика",
    "🔄 Перезагрузить A-Bot",
    "📂 Открытые позиции",
    "⏸️ Приостановить торги",
    "⚙️ Торговые параметры",
    "🇬🇧 English",
    "✅ Whitelist",
}

WHITELIST_TEXTS = {
    "⬅️ В главное меню",
    "✅ Добавить",
    "❌ Удалить",
    "✅ Добавить все монеты",
    "🔁 Вернуть список по умолчанию",
}

# --------------------------
# DB helpers
# --------------------------
CREATE_WHITE_LIST_SQL = """
CREATE TABLE IF NOT EXISTS white_list(
    id   INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    pair TEXT
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_WHITE_LIST_SQL)
        await db.commit()

async def fetch_white_list_pairs() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT pair FROM white_list ORDER BY pair ASC") as cur:
            rows = await cur.fetchall()
            return [r["pair"] for r in rows]

async def add_pairs(pairs: list[str]) -> tuple[int, int]:
    inserted = 0
    existed = 0
    if not pairs:
        return 0, 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q_marks = ",".join("?" for _ in pairs)
        async with db.execute(f"SELECT pair FROM white_list WHERE pair IN ({q_marks})", pairs) as cur:
            already = {r["pair"] for r in await cur.fetchall()}
        to_insert = [p for p in pairs if p not in already]
        existed = len(pairs) - len(to_insert)
        if to_insert:
            await db.executemany("INSERT INTO white_list(pair) VALUES(?)", [(p,) for p in to_insert])
            await db.commit()
            inserted = len(to_insert)
    return inserted, existed

async def delete_pairs(pairs: list[str]) -> int:
    if not pairs:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("DELETE FROM white_list WHERE pair = ?", [(p,) for p in pairs])
        await db.commit()
    return len(pairs)

# --------------------------
# STATE
# --------------------------
pending: dict[int, str] = {}
PAIR_RE = re.compile(r"[A-Za-z0-9._:-]{1,20}")

def parse_pairs(text: str) -> list[str]:
    raw = re.split(r"[,\s;]+", text.upper().strip())
    pairs = []
    for token in raw:
        if token and PAIR_RE.fullmatch(token):
            pairs.append(token)
    uniq = []
    seen = set()
    for p in pairs:
        if p not in seen:
            uniq.append(p); seen.add(p)
    return uniq

# --------------------------
# UTILS
# --------------------------
async def send_long_markdown(message: types.Message, text: str, chunk_limit: int = 3800):
    if len(text) <= chunk_limit:
        await message.answer(text, parse_mode="Markdown")
        return
    body = text.strip("` \n")
    lines = body.splitlines()
    buf, cur_len = [], 0
    for line in lines:
        if cur_len + len(line) + 1 > chunk_limit - 8:
            await message.answer("```\n" + "\n".join(buf) + "\n```", parse_mode="Markdown")
            buf, cur_len = [], 0
        buf.append(line); cur_len += len(line) + 1
    if buf:
        await message.answer("```\n" + "\n".join(buf) + "\n```", parse_mode="Markdown")

def items_to_text_block(items: list[str], width: int = 80, title: str | None = None) -> str:
    from textwrap import wrap
    joined = " ".join(items)
    rows = wrap(joined, width=width)
    text = "\n".join(rows)
    return f"{title}\n\n{text}" if title else text


async def send_long_text(message: types.Message, text: str, chunk_limit: int = 3800):
    if not text:
        await message.answer("Пусто.")
        return
    start = 0
    while start < len(text):
        end = min(start + chunk_limit, len(text))
        # старайся обрезать по переносу строки
        nl = text.rfind("\n", start, end)
        if nl != -1 and nl > start:
            end = nl
        await message.answer(text[start:end])
        start = end + 1 if end < len(text) and text[end] == "\n" else end

async def show_whitelist(message: types.Message):
    pairs = await fetch_white_list_pairs()
    if not pairs:
        await message.answer("Список whitelist пуст.", reply_markup=whitelist_menu)
        return
    header = f"📦 Кол-во пар в whitelist: {len(pairs)}"
    text = items_to_text_block(pairs, width=80, title=header)
    await send_long_text(message, text)
    await message.answer("Выберите действие:", reply_markup=whitelist_menu)


# --------------------------
# HANDLERS
# --------------------------
async def start_cmd(message: types.Message):
    await message.answer("Выберите действие:", reply_markup=main_menu)

async def main_menu_handler(message: types.Message):
    if message.text == "✅ Whitelist":
        await show_whitelist(message)
    else:
        await message.answer(f"Заглушка: {message.text}", reply_markup=main_menu)

async def whitelist_handler(message: types.Message):
    txt = message.text
    if txt == "⬅️ В главное меню":
        pending.pop(message.from_user.id, None)
        await message.answer("Выберите действие:", reply_markup=main_menu)
    elif txt == "✅ Добавить":
        pending[message.from_user.id] = "add"
        await message.answer("Введи пары для добавления:")
    elif txt == "❌ Удалить":
        pending[message.from_user.id] = "del"
        await message.answer("Введи пары для удаления:")
    else:
        action = pending.get(message.from_user.id)
        if action == "add":
            pairs = parse_pairs(txt)
            added, existed = await add_pairs(pairs)
            await message.answer(f"✅ Добавлено: {added}, уже были: {existed}", reply_markup=whitelist_menu)
            pending.pop(message.from_user.id, None)
            await show_whitelist(message)
        elif action == "del":
            pairs = parse_pairs(txt)
            removed = await delete_pairs(pairs)
            await message.answer(f"🗑 Удалено: {removed}", reply_markup=whitelist_menu)
            pending.pop(message.from_user.id, None)
            await show_whitelist(message)

# --------------------------
# APP
# --------------------------
async def main():
    if not API_TOKEN:
        raise RuntimeError("API_TOKEN пуст. Укажи его в .env (API_TOKEN=...)")
    await init_db()
    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()
    dp.message.register(start_cmd, CommandStart())
    dp.message.register(start_cmd, Command("menu"))
    dp.message.register(main_menu_handler, F.text.in_(MENU_TEXTS))
    dp.message.register(whitelist_handler, F.text.in_(WHITELIST_TEXTS))
    dp.message.register(whitelist_handler, F.text)
    logging.info("Bot polling started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())