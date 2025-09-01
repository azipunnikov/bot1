import os
import re
import asyncio
import logging
from textwrap import wrap

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
import aiosqlite

# --------------------------
# CONFIG
# --------------------------
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()

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

trade_params_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⬅️ В главное меню")],
        [KeyboardButton(text="🧩 Комплексное редактирование"), KeyboardButton(text="❓ Описание")],
        # сетка полей (пока заглушки для редактирования отдельных параметров)
        [KeyboardButton(text="timeout_socket"), KeyboardButton(text="min_bnb"), KeyboardButton(text="min_balance")],
        [KeyboardButton(text="position_size"), KeyboardButton(text="min_order"), KeyboardButton(text="min_price")],
        [KeyboardButton(text="min_daily_percent"), KeyboardButton(text="daily_percent"), KeyboardButton(text="auto_daily_percent")],
        [KeyboardButton(text="order_timer"), KeyboardButton(text="min_value"), KeyboardButton(text="sell_up")],
        [KeyboardButton(text="buy_down"), KeyboardButton(text="max_trade_pairs"), KeyboardButton(text="auto_trade_pairs")],
        [KeyboardButton(text="progressive_max_pairs"), KeyboardButton(text="delta_percent"), KeyboardButton(text="delta_deep")],
        [KeyboardButton(text="num_aver"), KeyboardButton(text="step_aver"), KeyboardButton(text="max_aver")],
        [KeyboardButton(text="quantity_aver"), KeyboardButton(text="average_percent"), KeyboardButton(text="trailing_stop")],
        [KeyboardButton(text="trailing_percent"), KeyboardButton(text="trailing_part"), KeyboardButton(text="trailing_price")],
        [KeyboardButton(text="new_listing"), KeyboardButton(text="listing_order"), KeyboardButton(text="max_buy_listing")],
        [KeyboardButton(text="user_order"), KeyboardButton(text="fiat_currencies"), KeyboardButton(text="quote_asset")],
        [KeyboardButton(text="double_asset"), KeyboardButton(text="pump_detector"), KeyboardButton(text="pump_order")],
        [KeyboardButton(text="pump_up"), KeyboardButton(text="max_pump_pairs"), KeyboardButton(text="trailing_pump")],
        [KeyboardButton(text="tg_template"), KeyboardButton(text="individual_depth"), KeyboardButton(text="reinvest_position")],
        [KeyboardButton(text="reinvest_percent"), KeyboardButton(text="trading_view"), KeyboardButton(text="max_trading_view")],
        [KeyboardButton(text="row_sell"), KeyboardButton(text="sell_count"), KeyboardButton(text="trailing_value")],
        [KeyboardButton(text="signals"), KeyboardButton(text="max_signals"), KeyboardButton(text="volatility")],
        [KeyboardButton(text="delisting_sale"), KeyboardButton(text="conf_key"), KeyboardButton(text="dev_signals")],
        [KeyboardButton(text="max_dev_signals"), KeyboardButton(text="average_dev_signals")]
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

TRADE_PARAMS_TEXTS = {btn.text for row in trade_params_menu.keyboard for btn in row}  # все подписи из клавы

# --------------------------
# DB helpers
# --------------------------
CREATE_WHITE_LIST_SQL = """
CREATE TABLE IF NOT EXISTS white_list(
    id   INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    pair TEXT
);
"""

CREATE_TRADE_PARAMS_SQL = """
CREATE TABLE IF NOT EXISTS trade_params(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    timeout_socket TEXT,
    min_bnb TEXT,
    min_balance TEXT,
    position_size TEXT,
    min_order TEXT,
    min_price TEXT,
    min_daily_percent TEXT,
    daily_percent TEXT,
    auto_daily_percent TEXT,
    order_timer TEXT,
    min_value TEXT,
    sell_up TEXT,
    buy_down TEXT,
    max_trade_pairs TEXT,
    auto_trade_pairs BOOLEAN,
    progressive_max_pairs BOOLEAN,
    delta_percent BOOLEAN,
    delta_deep BOOLEAN,
    num_aver BOOLEAN,
    step_aver TEXT,
    max_aver TEXT,
    quantity_aver TEXT,
    average_percent TEXT,
    trailing_stop BOOLEAN,
    trailing_percent TEXT,
    trailing_part TEXT,
    trailing_price TEXT,
    new_listing BOOLEAN,
    listing_order TEXT,
    max_buy_listing TEXT,
    user_order BOOLEAN,
    fiat_currencies TEXT,
    quote_asset TEXT,
    double_asset BOOLEAN,
    pump_detector BOOLEAN,
    pump_order TEXT,
    pump_up TEXT,
    max_pump_pairs TEXT,
    trailing_pump BOOLEAN,
    tg_template TEXT,
    individual_depth BOOLEAN,
    reinvest_position BOOLEAN,
    reinvest_percent TEXT,
    trading_view BOOLEAN,
    max_trading_view TEXT,
    row_sell TEXT,
    sell_count BOOLEAN,
    trailing_value TEXT,
    signals BOOLEAN,
    max_signals TEXT,
    volatility BOOLEAN,
    delisting_sale BOOLEAN,
    conf_key TEXT,
    dev_signals BOOLEAN,
    max_dev_signals TEXT,
    average_dev_signals BOOLEAN
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_WHITE_LIST_SQL)
        await db.execute(CREATE_TRADE_PARAMS_SQL)
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

async def fetch_trade_params() -> dict[str, str | int | float | bool | None]:
    """
    Берём ПЕРВУЮ строку из trade_params (как текущий профиль настроек).
    Возвращаем dict: {column: value}
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trade_params ORDER BY id ASC LIMIT 1") as cur:
            row = await cur.fetchone()
            if row is None:
                return {}
            return dict(row)

# --------------------------
# STATE + parsing
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
async def send_long_text(message: types.Message, text: str, chunk_limit: int = 3800):
    if not text:
        await message.answer("Пусто.")
        return
    start = 0
    while start < len(text):
        end = min(start + chunk_limit, len(text))
        nl = text.rfind("\n", start, end)
        if nl != -1 and nl > start:
            end = nl
        await message.answer(text[start:end])
        start = end + 1 if end < len(text) and text[end] == "\n" else end

def items_to_text_block(items: list[str], width: int = 80, title: str | None = None) -> str:
    joined = " ".join(items)
    rows = wrap(joined, width=width)
    text = "\n".join(rows)
    return f"{title}\n\n{text}" if title else text

def _bool_to_ru(val) -> str:
    if val in (1, "1", True, "true", "True", "YES", "Да", "да"):
        return "Да"
    if val in (0, "0", False, "false", "False", "NO", "Нет", "нет", None, ""):
        return "Нет"
    return str(val)

def format_trade_params(tp: dict[str, object]) -> str:
    """
    Делаем две секции как на скрине:
    1) "Конфигурационные ключи" — короткий список ключевых флагов/конфига
    2) "Торговые параметры" — полный список
    """
    if not tp:
        return "Профиль торговых параметров отсутствует. Добавь строку в trade_params."

    # Секция 1: конфигурационные ключи (пример — можешь менять состав)
    conf_lines = [
        "Конфигурационные ключи:",
        f"—conf quantity_aver {tp.get('quantity_aver', '')}",
        f"—conf trailing_stop {_bool_to_ru(tp.get('trailing_stop'))}",
        f"—conf pump_value {tp.get('pump_up', '')}",
        f"—conf signals {_bool_to_ru(tp.get('signals'))}",
        f"—conf sell_up {tp.get('sell_up', '')}",
        f"—conf volatility {_bool_to_ru(tp.get('volatility'))}",
        f"—conf buy_down {tp.get('buy_down', '')}",
        f"—conf step_aver {tp.get('step_aver', '')}",
        f"—conf daily_percent {tp.get('daily_percent', '')}",
    ]

    # Секция 2: полный список (читаемые подписи)
    # Порядок полей — примерно как в твоей схеме/скрине
    field_titles: list[tuple[str, str, bool]] = [
        ("min_bnb", "min_bnb", False),
        ("min_balance", "min_balance", False),
        ("position_size", "position_size", False),
        ("min_order", "min_order", False),
        ("min_price", "min_price", False),
        ("min_daily_percent", "min_daily_percent", False),
        ("daily_percent", "daily_percent", False),
        ("auto_daily_percent", "auto_daily_percent", False),
        ("order_timer", "order_timer", False),
        ("min_value", "min_value", False),
        ("sell_up", "sell_up", False),
        ("buy_down", "buy_down", False),
        ("max_trade_pairs", "max_trade_pairs", False),
        ("auto_trade_pairs", "auto_trade_pairs", True),
        ("progressive_max_pairs", "progressive_max_pairs", True),
        ("delta_percent", "delta_percent", True),
        ("delta_deep", "delta_deep", True),
        ("num_aver", "num_aver", True),
        ("step_aver", "step_aver", False),
        ("max_aver", "max_aver", False),
        ("quantity_aver", "quantity_aver", False),
        ("average_percent", "average_percent", False),
        ("trailing_stop", "trailing_stop", True),
        ("trailing_percent", "trailing_percent", False),
        ("trailing_part", "trailing_part", False),
        ("trailing_price", "trailing_price", False),
        ("new_listing", "new_listing", True),
        ("listing_order", "listing_order", False),
        ("max_buy_listing", "max_buy_listing", False),
        ("user_order", "user_order", True),
        ("fiat_currencies", "fiat_currencies", False),
        ("quote_asset", "quote_asset", False),
        ("double_asset", "double_asset", True),
        ("pump_detector", "pump_detector", True),
        ("pump_order", "pump_order", False),
        ("pump_up", "pump_up", False),
        ("max_pump_pairs", "max_pump_pairs", False),
        ("trailing_pump", "trailing_pump", True),
        ("tg_template", "tg_template", False),
        ("individual_depth", "individual_depth", True),
        ("reinvest_position", "reinvest_position", True),
        ("reinvest_percent", "reinvest_percent", False),
        ("trading_view", "trading_view", True),
        ("max_trading_view", "max_trading_view", False),
        ("row_sell", "row_sell", False),
        ("sell_count", "sell_count", True),
        ("trailing_value", "trailing_value", False),
        ("signals", "signals", True),
        ("max_signals", "max_signals", False),
        ("volatility", "volatility", True),
        ("delisting_sale", "delisting_sale", True),
        ("conf_key", "conf_key", False),
        ("dev_signals", "dev_signals", True),
        ("max_dev_signals", "max_dev_signals", False),
        ("average_dev_signals", "average_dev_signals", True),
        ("timeout_socket", "timeout_socket", False),
    ]

    trade_lines = ["Торговые параметры:"]
    for field, title, is_bool in field_titles:
        val = tp.get(field, "")
        trade_lines.append(f"{title}: {_bool_to_ru(val) if is_bool else (val if val is not None else '')}")

    # Склеиваем
    conf_text = "\n".join(conf_lines)
    trade_text = "\n".join(trade_lines)
    return conf_text + "\n\n" + trade_text

async def show_trade_params(message: types.Message):
    tp = await fetch_trade_params()
    text = format_trade_params(tp)
    await send_long_text(message, text)
    await message.answer("Выберите параметр для изменений или используйте комплексное редактирование:", reply_markup=trade_params_menu)

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
    txt = message.text

    if txt == "✅ Whitelist":
        await show_whitelist(message)
        return

    if txt == "⚙️ Торговые параметры":
        await show_trade_params(message)
        return

    # Остальные пока заглушки
    await message.answer(f"Заглушка: {txt}", reply_markup=main_menu)

async def whitelist_handler(message: types.Message):
    txt = message.text

    if txt == "⬅️ В главное меню":
        pending.pop(message.from_user.id, None)
        await message.answer("Выберите действие:", reply_markup=main_menu)
        return

    if txt == "✅ Добавить":
        pending[message.from_user.id] = "add"
        await message.answer("Введи пары для добавления (через пробел/запятую/переносы):")
        return

    if txt == "❌ Удалить":
        pending[message.from_user.id] = "del"
        await message.answer("Введи пары для удаления (через пробел/запятую/переносы):")
        return

    if txt == "✅ Добавить все монеты":
        await message.answer("Заглушка: импорт всех монет.", reply_markup=whitelist_menu)
        return

    if txt == "🔁 Вернуть список по умолчанию":
        await message.answer("Заглушка: восстановление дефолтного списка.", reply_markup=whitelist_menu)
        return

    # режим add/del
    action = pending.get(message.from_user.id)
    if action in ("add", "del"):
        pairs = parse_pairs(txt)
        if not pairs:
            await message.answer("Не распознал пары. Пример: AAPL TSLA NVDA")
            return
        if action == "add":
            added, existed = await add_pairs(pairs)
            await message.answer(f"✅ Добавлено: {added}\nℹ️ Уже были: {existed}", reply_markup=whitelist_menu)
        else:
            removed = await delete_pairs(pairs)
            await message.answer(f"🗑 Удалено: {removed}", reply_markup=whitelist_menu)

        pending.pop(message.from_user.id, None)
        await show_whitelist(message)

async def trade_params_handler(message: types.Message):
    txt = message.text

    if txt == "⬅️ В главное меню":
        await message.answer("Выберите действие:", reply_markup=main_menu)
        return

    if txt == "🧩 Комплексное редактирование":
        await message.answer("Заглушка: откроем последовательный мастер-редактор всех полей.", reply_markup=trade_params_menu)
        return

    if txt == "❓ Описание":
        await message.answer("Заглушка: покажем справку по каждому параметру.", reply_markup=trade_params_menu)
        return

    # Любая кнопка-имя поля — пока заглушка
    if txt in TRADE_PARAMS_TEXTS:
        await message.answer(f"Редактирование параметра `{txt}` пока не реализовано.", reply_markup=trade_params_menu)
        return

# --------------------------
# APP
# --------------------------
async def main():
    if not API_TOKEN:
        raise RuntimeError("API_TOKEN пуст. Укажи его в .env (API_TOKEN=...)")
    await init_db()

    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()

    # Команды
    dp.message.register(start_cmd, CommandStart())
    dp.message.register(start_cmd, Command("menu"))

    # Основное меню
    dp.message.register(main_menu_handler, F.text.in_(MENU_TEXTS))

    # Подменю whitelist
    dp.message.register(whitelist_handler, F.text.in_(WHITELIST_TEXTS))
    dp.message.register(whitelist_handler, F.text)  # ввод тикеров

    # Подменю торговых параметров
    dp.message.register(trade_params_handler, F.text.in_(TRADE_PARAMS_TEXTS))

    logging.info("Bot polling started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
