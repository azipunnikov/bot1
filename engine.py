# engine.py
import os
import time
import math
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple

import aiosqlite
from ib_insync import IB, Stock, Ticker

from app import buy_now, sell_now

log = logging.getLogger("engine")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

DB_PATH = os.getenv("DB_PATH", "bot.db")

# Параметры стратегии из .env (с дефолтами)
BASE_QTY = int(os.getenv("BASE_QTY", "1"))
DCA_STEP_PCT = float(os.getenv("DCA_STEP_PCT", "0.02"))         # 2% вниз — докупка
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.02"))   # 2% вверх — частичная фиксация
USE_TIMESTAMP_ID = os.getenv("USE_TIMESTAMP_ID", "true").lower() == "true"

# Какие статусы считаем «открытыми» локально
OPEN_STATUSES = {"Submitted", "PreSubmitted", "PendingSubmit", "Inactive"}

# ---------------------------
# Вспомогательные структуры
# ---------------------------
@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float
    last: float

# ---------------------------
# DB helpers (SQLite, aiosqlite)
# ---------------------------
async def _db() -> aiosqlite.Connection:
    return await aiosqlite.connect(DB_PATH)

async def ensure_schema():
    async with await _db() as cx:
        await cx.execute("""
        CREATE TABLE IF NOT EXISTS white_list(
            pair TEXT PRIMARY KEY
        );
        """)
        await cx.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orderId INTEGER,
            permId INTEGER,
            symbol TEXT,
            side TEXT,
            type TEXT,
            lmtPrice REAL,
            tif TEXT,
            outsideRth INTEGER,
            status TEXT,
            filled REAL,
            remaining REAL,
            avgFillPrice REAL,
            lastFillPrice REAL,
            whyHeld TEXT,
            created_at INTEGER,
            updated_at INTEGER
        );
        """)
        await cx.execute("""
        CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
        """)
        await cx.execute("""
        CREATE TABLE IF NOT EXISTS positions(
            symbol TEXT PRIMARY KEY,
            qty REAL,
            avg_cost REAL,
            last REAL,
            updated_at INTEGER
        );
        """)
        await cx.commit()

async def get_white_list() -> List[str]:
    async with await _db() as cx:
        rows = await cx.execute_fetchall("SELECT pair FROM white_list")
        return [r[0] for r in rows]

async def has_open_local_order(symbol: str) -> bool:
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    sql = f"SELECT 1 FROM orders WHERE symbol=? AND status IN ({placeholders}) LIMIT 1"
    async with await _db() as cx:
        row = await cx.execute_fetchone(sql, (symbol, *OPEN_STATUSES))
        return row is not None

async def upsert_order_from_info(info: dict):
    now = int(time.time())
    # простая UPSERT по (symbol, orderId)
    async with await _db() as cx:
        await cx.execute("""
        INSERT INTO orders (orderId, permId, symbol, side, type, lmtPrice, tif, outsideRth,
                            status, filled, remaining, avgFillPrice, lastFillPrice, whyHeld,
                            created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(orderId) DO UPDATE SET
            status=excluded.status,
            filled=excluded.filled,
            remaining=excluded.remaining,
            avgFillPrice=excluded.avgFillPrice,
            lastFillPrice=excluded.lastFillPrice,
            updated_at=excluded.updated_at
        """, (
            info.get("orderId"),
            info.get("permId"),
            info.get("symbol"),
            info.get("action"),
            info.get("orderType"),
            info.get("lmtPrice"),
            info.get("tif"),
            1 if info.get("outsideRth") else 0,
            info.get("status"),
            info.get("filled"),
            info.get("remaining"),
            info.get("avgFillPrice"),
            info.get("lastFillPrice"),
            info.get("whyHeld"),
            now, now
        ))
        await cx.commit()

async def upsert_position(p: Position):
    now = int(time.time())
    async with await _db() as cx:
        await cx.execute("""
        INSERT INTO positions(symbol, qty, avg_cost, last, updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET
            qty=excluded.qty,
            avg_cost=excluded.avg_cost,
            last=excluded.last,
            updated_at=excluded.updated_at
        """, (p.symbol, p.qty, p.avg_cost, p.last, now))
        await cx.commit()

async def get_position(symbol: str) -> Optional[Position]:
    async with await _db() as cx:
        row = await cx.execute_fetchone(
            "SELECT symbol, qty, avg_cost, last FROM positions WHERE symbol=?",
            (symbol,))
        if not row:
            return None
        return Position(symbol=row[0], qty=row[1] or 0.0, avg_cost=row[2] or 0.0, last=row[3] or 0.0)

async def mark_missing_open_orders_as_killed(open_keys: List[Tuple[int, int]]):
    """
    open_keys: список (orderId, permId), которые реально открыты в IB.
    В БД найдём ордера в OPEN_STATUSES, которых нет в open_keys — и пометим Killed.
    """
    async with await _db() as cx:
        rows = await cx.execute_fetchall(
            f"SELECT rowid, orderId, permId FROM orders WHERE status IN ({','.join('?'*len(OPEN_STATUSES))})",
            (*OPEN_STATUSES,))
        to_kill = []
        ib_set = {(oid or -1, pid or -1) for (oid, pid) in open_keys}
        for rowid, orderId, permId in rows:
            key = (orderId or -1, permId or -1)
            if key not in ib_set:
                to_kill.append(rowid)
        if to_kill:
            qmarks = ",".join("?" * len(to_kill))
            await cx.execute(f"UPDATE orders SET status='Killed', updated_at=? WHERE rowid IN ({qmarks})",
                             (int(time.time()), *to_kill))
            await cx.commit()
            log.info("Reconcile: marked %d orders as Killed (missing in IB)", len(to_kill))

# ---------------------------
# IB helpers (цена и reconcile)
# ---------------------------
async def _ib_connect() -> IB:
    ib = IB()
    await ib.connectAsync("127.0.0.1", 7497, clientId=103)
    return ib

async def _qualify(ib: IB, symbol: str):
    c = Stock(symbol, "SMART", "USD", primaryExchange="NASDAQ")
    await ib.qualifyContractsAsync(c)
    return c

async def get_last(ib: IB, symbol: str) -> float:
    c = await _qualify(ib, symbol)
    t: Ticker = await ib.reqMktDataAsync(c, "", snapshot=True)
    # ждём пока придёт last/close
    for _ in range(50):
        px = t.last or t.close or t.marketPrice()
        if px and px > 0:
            return float(px)
        await asyncio.sleep(0.1)
    # если так и не пришло — вернём 0
    return float(t.last or t.close or 0.0)

async def reconcile_orders_with_ib():
    """
    1) Получаем открытые ордера из IB.
    2) Обновляем/вставляем их в БД.
    3) Те, которых нет в IB, но в БД числятся как open — помечаем Killed.
    """
    try:
        ib = await _ib_connect()
    except Exception as e:
        log.error("reconcile connect failed: %s", e)
        return

    try:
        await ib.reqOpenOrdersAsync()
        open_trades = list(ib.openTrades())
        open_keys = []
        for t in open_trades:
            info = {
                "orderId": getattr(t.order, "orderId", None),
                "permId": getattr(t.order, "permId", None),
                "action": t.order.action,
                "symbol": t.contract.symbol,
                "orderType": t.order.orderType,
                "lmtPrice": getattr(t.order, "lmtPrice", None),
                "tif": getattr(t.order, "tif", None),
                "outsideRth": getattr(t.order, "outsideRth", None),
                "status": getattr(t.orderStatus, "status", None),
                "filled": getattr(t.orderStatus, "filled", None),
                "remaining": getattr(t.orderStatus, "remaining", None),
                "avgFillPrice": getattr(t.orderStatus, "avgFillPrice", None),
                "lastFillPrice": getattr(t.orderStatus, "lastFillPrice", None),
                "whyHeld": getattr(t.orderStatus, "whyHeld", None),
            }
            await upsert_order_from_info(info)
            open_keys.append((info["orderId"], info["permId"]))

        await mark_missing_open_orders_as_killed(open_keys)
    finally:
        ib.disconnect()

async def reconcile_positions_with_ib(symbols: List[str]):
    """
    Обновляем таблицу positions из IB.
    """
    try:
        ib = await _ib_connect()
    except Exception as e:
        log.error("reconcile positions connect failed: %s", e)
        return

    try:
        # получаем last для whitelisted тикеров
        for sym in symbols:
            try:
                last = await get_last(ib, sym)
            except Exception as e:
                log.error("ticker %s last failed: %s", sym, e)
                last = 0.0

            # из IB заберём позицию (если есть)
            qty = 0.0
            avg = 0.0
            for p in await ib.reqPositionsAsync():
                if getattr(p.contract, "symbol", "") == sym:
                    qty = float(p.position or 0.0)
                    avg = float(p.avgCost or 0.0)
                    break

            await upsert_position(Position(sym, qty, avg, last))
    finally:
        ib.disconnect()

# ---------------------------
# DCA цикл
# ---------------------------
async def dca_loop():
    await ensure_schema()
    log.info("DCA loop started (BASE_QTY=%s, DCA_STEP=%.2f%%, TP=%.2f%%)",
             BASE_QTY, DCA_STEP_PCT*100, TAKE_PROFIT_PCT*100)

    while True:
        try:
            # 1) reconcile
            await reconcile_orders_with_ib()
            symbols = await get_white_list()
            await reconcile_positions_with_ib(symbols)

            # 2) по каждому тикеру — максимум 1 активный ордер
            for sym in symbols:
                try:
                    if await has_open_local_order(sym):
                        log.info("[%s] skip: already has open order", sym)
                        continue

                    pos = await get_position(sym)
                    if not pos:
                        log.info("[%s] no local pos row (will be created by reconcile), skip this turn", sym)
                        continue

                    # Если позиции нет — первая покупка
                    if pos.qty <= 0:
                        # первая заявка — LIMIT на текущий last (чуть ниже на «тик»)
                        entry_px = pos.last
                        if not entry_px or entry_px <= 0:
                            log.info("[%s] no last price yet, skip", sym)
                            continue
                        limit = round(entry_px * 0.999, 2)  # на цент ниже
                        log.info("[%s] first BUY: qty=%s @%s (last=%.2f)", sym, BASE_QTY, limit, entry_px)
                        try:
                            info = await buy_now(sym, BASE_QTY, limit, use_timestamp_id=USE_TIMESTAMP_ID)
                            await upsert_order_from_info(info)
                        except Exception as e:
                            log.error("[%s] first BUY failed: %s", sym, e)
                        continue

                    # Есть позиция: проверим усреднение и тейк-профит
                    avg = pos.avg_cost or 0.0
                    last = pos.last or 0.0
                    if avg <= 0 or last <= 0:
                        log.info("[%s] avg/last invalid (avg=%.4f last=%.4f), skip", sym, avg, last)
                        continue

                    # DCA вниз
                    if last <= avg * (1 - DCA_STEP_PCT):
                        limit = round(last, 2)
                        log.info("[%s] DCA BUY: qty=%s @%s (avg=%.2f last=%.2f)", sym, BASE_QTY, limit, avg, last)
                        try:
                            info = await buy_now(sym, BASE_QTY, limit, use_timestamp_id=USE_TIMESTAMP_ID)
                            await upsert_order_from_info(info)
                            continue
                        except Exception as e:
                            log.error("[%s] DCA BUY failed: %s", sym, e)

                    # Тейк-профит вверх
                    if last >= avg * (1 + TAKE_PROFIT_PCT) and pos.qty > 0:
                        sell_qty = min(BASE_QTY, math.floor(pos.qty))
                        if sell_qty <= 0:
                            log.info("[%s] TP: nothing to sell (qty=%.2f)", sym, pos.qty)
                        else:
                            limit = round(last, 2)
                            log.info("[%s] TP SELL: qty=%s @%s (avg=%.2f last=%.2f)", sym, sell_qty, limit, avg, last)
                            try:
                                info = await sell_now(sym, sell_qty, limit, use_timestamp_id=USE_TIMESTAMP_ID)
                                await upsert_order_from_info(info)
                                continue
                            except Exception as e:
                                log.error("[%s] TP SELL failed: %s", sym, e)

                except Exception as e:
                    log.error("[%s] symbol loop error: %s", sym, e)

        except Exception as e:
            log.error("DCA cycle error: %s", e)

        # ⬇️ как просил: всегда 2 сек пауза
        await asyncio.sleep(2)

# ---------------------------
# Пример запуска
# ---------------------------
if __name__ == "__main__":
    asyncio.run(dca_loop())
