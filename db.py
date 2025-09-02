# db.py
import os
import json
from typing import Any, Dict
import aiosqlite

# Путь к базе: берём из .env или по умолчанию bot.db
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()

# ---------- схемы ----------
CREATE_SYMBOLS_SQL = """
CREATE TABLE IF NOT EXISTS symbols(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    pair TEXT UNIQUE,
    baseAsset TEXT, quoteAsset TEXT, stepSize TEXT, tickSize TEXT, minNotional TEXT,
    priceChangePercent TEXT, bidPrice TEXT, askPrice TEXT, value TEXT, averagePrice TEXT,
    buyPrice TEXT, sellPrice TEXT, trailingPrice TEXT, allQuantity TEXT, freeQuantity TEXT,
    lockQuantity TEXT, orderId TEXT, profit TEXT, totalQuote TEXT, stepAveraging TEXT,
    numAveraging TEXT, statusOrder TEXT, timer TEXT, pumpDetector TEXT, pumpSignal TEXT,
    lowPrice TEXT, multiplierDown TEXT, multiplierUp TEXT, lockedQuote TEXT, twSignal TEXT,
    oco BOOLEAN, lastEvent TEXT, manualReinvest BOOLEAN, rowSell TEXT, sellCount TEXT,
    bidMultiplierDown TEXT, askMultiplierUp TEXT, signal TEXT, averagingBlocking BOOLEAN,
    rowTimer TEXT, delisting BOOLEAN, volatility TEXT
);
"""

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

# Новая таблица под ордера IB
CREATE_ORDERS_SQL = """
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    orderId INTEGER,
    permId  INTEGER UNIQUE,          -- ключ для UPSERT
    action TEXT,
    symbol TEXT,
    orderType TEXT,
    lmtPrice REAL,
    tif TEXT,
    outsideRth INTEGER,
    status TEXT,
    filled REAL,
    remaining REAL,
    avgFillPrice REAL,
    lastFillPrice REAL,
    whyHeld TEXT,
    raw_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_orders_permId ON orders(permId);
CREATE INDEX IF NOT EXISTS idx_orders_orderId ON orders(orderId);
"""

# ---------- инициализация ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SYMBOLS_SQL)
        await db.executescript(CREATE_WHITE_LIST_SQL)
        await db.executescript(CREATE_TRADE_PARAMS_SQL)
        await db.executescript(CREATE_ORDERS_SQL)
        await db.commit()

# ---------- helpers ----------
def _bool_to_int(v: Any) -> int | None:
    if v is None:
        return None
    return 1 if bool(v) else 0

# ---------- upsert ордера ----------
async def upsert_order(info: Dict[str, Any]) -> None:
    """
    Сохранить/обновить запись об ордере по permId.
    Ожидает словарь вида, который возвращает _trade_summary в app.py.
    """
    perm_id = info.get("permId")
    if perm_id is None:
        raise ValueError("permId is required for upsert_order")

    payload = {
        "orderId":       info.get("orderId"),
        "permId":        perm_id,
        "action":        info.get("action"),
        "symbol":        info.get("symbol"),
        "orderType":     info.get("orderType"),
        "lmtPrice":      info.get("lmtPrice"),
        "tif":           info.get("tif"),
        "outsideRth":    _bool_to_int(info.get("outsideRth")),
        "status":        info.get("status"),
        "filled":        info.get("filled"),
        "remaining":     info.get("remaining"),
        "avgFillPrice":  info.get("avgFillPrice"),
        "lastFillPrice": info.get("lastFillPrice"),
        "whyHeld":       info.get("whyHeld"),
        "raw_json":      json.dumps(info, ensure_ascii=False),
    }

    cols = ", ".join(payload.keys())
    placeholders = ", ".join([f":{k}" for k in payload.keys()])
    set_clause = ", ".join([f"{k}=excluded.{k}" for k in payload.keys() if k != "permId"])

    sql = f"""
    INSERT INTO orders ({cols})
    VALUES ({placeholders})
    ON CONFLICT(permId) DO UPDATE SET
        {set_clause},
        updated_at = CURRENT_TIMESTAMP
    ;
    """

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute(sql, payload)
        await db.commit()

# ---------- оставшиеся утилиты из твоего db.py ----------
async def fetch_trade_params() -> dict[str, Any]:
    """Берём первую строку trade_params как активный профиль."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trade_params ORDER BY id ASC LIMIT 1") as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}

async def upsert_symbol_from_ib(symbol: str, qty: float, avg_price: float):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM symbols WHERE pair=?", (symbol,)) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE symbols SET averagePrice=?, allQuantity=?, freeQuantity=?, statusOrder=? WHERE pair=?",
                (str(avg_price), str(qty), str(qty), "OPEN" if qty != 0 else "FLAT", symbol),
            )
        else:
            await db.execute(
                "INSERT INTO symbols(pair, averagePrice, allQuantity, freeQuantity, statusOrder) VALUES(?,?,?,?,?)",
                (symbol, str(avg_price), str(qty), str(qty), "OPEN" if qty != 0 else "FLAT"),
            )
        await db.commit()

async def load_avg_qty_for(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """Возвращает {SYM: (avgPrice, freeQty)}"""
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT pair, averagePrice, freeQuantity FROM symbols WHERE pair IN ({placeholders})", symbols
        ) as cur:
            rows = await cur.fetchall()
            return {
                r["pair"]: (float(r["averagePrice"] or 0), float(r["freeQuantity"] or 0))
                for r in rows
            }

async def set_avg_qty(symbol: str, avg: float, qty: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE symbols SET averagePrice=?, freeQuantity=?, allQuantity=? WHERE pair=?",
            (str(avg), str(qty), str(qty), symbol),
        )
        await db.commit()

async def fetch_white_list_pairs() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT pair FROM white_list ORDER BY pair ASC") as cur:
            return [r["pair"] for r in await cur.fetchall()]
