import os
import asyncio
import logging
from decimal import Decimal
from typing import Callable

from ib_insync import IB, Stock, MarketOrder, LimitOrder, Ticker

from db import (
    init_db, fetch_trade_params, upsert_symbol_from_ib,
    load_avg_qty_for, set_avg_qty
)

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "101"))

class DCAEngine:
    def __init__(self):
        self.ib = IB()
        self.running = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.notify: Callable[[str], asyncio.Future] | None = None

    # ---- utils ----
    @staticmethod
    def _pct_drop(from_price: Decimal, to_price: Decimal) -> Decimal:
        if from_price <= 0 or to_price <= 0:
            return Decimal("0")
        return (from_price - to_price) / from_price * Decimal("100")

    async def _params(self):
        """Читаем параметры из БД на каждый цикл."""
        tp = await fetch_trade_params()
        # разумные дефолты если строки ещё нет
        after_hours = str(tp.get("trading_view", "1")).lower() in ("1","true","yes")  # пример флага
        tp_pct = Decimal(str(tp.get("daily_percent", "1.0")))
        avg_trig = Decimal(str(tp.get("average_percent", "2.0")))
        avg_mult = Decimal(str(tp.get("quantity_aver", "1.0")))
        base_qty = Decimal(str(tp.get("position_size", "1")))
        check_sec = float(str(tp.get("order_timer", "5")))
        return after_hours, tp_pct, avg_trig, avg_mult, base_qty, check_sec

    # ---- IB connect ----
    async def connect(self):
        if not self.ib.isConnected():
            await self.ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
            logging.info("Connected to IBKR")

    async def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            logging.info("Disconnected from IBKR")

    async def reconcile(self) -> list[str]:
        """сверяем IB позиции в БД; возвращаем список активных символов"""
        await self.connect()
        pos = await self.ib.reqPositionsAsync()
        active: list[str] = []
        for p in pos:
            sym = p.contract.symbol
            qty = float(p.position)
            avg = float(p.avgCost or 0)
            await upsert_symbol_from_ib(sym, qty, avg)
            if qty != 0:
                active.append(sym)
        # символы, которые в БД OPEN, но в IB qty==0, можно дополнительно гасить — опущено
        return active

    async def ensure_tp(self, symbol: str, avg: Decimal, qty: Decimal, tp_pct: Decimal, outside: bool):
        if qty <= 0 or avg <= 0:
            return
        tp_price = (avg * (Decimal("1") + tp_pct / Decimal("100"))).quantize(Decimal("0.01"))
        contract = Stock(symbol, "SMART", "USD")
        order = LimitOrder("SELL", float(qty), float(tp_price), tif="GTC", outsideRth=outside)
        trade = self.ib.placeOrder(contract, order)
        logging.info(f"[{symbol}] TP placed {qty}@{tp_price}, permId={trade.order.permId}")

    async def cycle(self):
        await self.connect()
        while self.running.is_set():
            try:
                outside, tp_pct, trig_pct, mult, base_qty, check_sec = await self._params()

                active = await self.reconcile()
                if not active:
                    await asyncio.sleep(check_sec)
                    continue

                contracts = [Stock(s, "SMART", "USD") for s in active]
                tickers: list[Ticker] = await self.ib.reqTickersAsync(*contracts)

                avgqty = await load_avg_qty_for(active)

                for t in tickers:
                    sym = t.contract.symbol
                    last = Decimal(str(t.last)) if t.last else Decimal("0")
                    avg0, qty0 = avgqty.get(sym, (0.0, 0.0))
                    avg = Decimal(str(avg0))
                    qty = Decimal(str(qty0))

                    if last <= 0 or avg <= 0 or qty <= 0:
                        # просто убедимся в наличии TP
                        await self.ensure_tp(sym, avg, qty, tp_pct, outside)
                        continue

                    drop = self._pct_drop(avg, last)
                    if drop >= trig_pct:
                        buy_qty = (base_qty * mult).quantize(Decimal("1"))
                        self.ib.placeOrder(Stock(sym, "SMART", "USD"),
                                           MarketOrder("BUY", int(buy_qty), outsideRth=outside))
                        if self.notify:
                            await self.notify(f"🟢 [{sym}] усреднение {buy_qty} @ mkt (drop {drop:.2f}%)")
                        logging.info(f"[{sym}] AVERAGE BUY {buy_qty} (drop {drop:.2f}%)")
                        new_qty = qty + buy_qty
                        new_avg = ((avg * qty) + (last * buy_qty)) / new_qty
                        await set_avg_qty(sym, float(new_avg), float(new_qty))
                        await self.ensure_tp(sym, new_avg, new_qty, tp_pct, outside)
                    else:
                        await self.ensure_tp(sym, avg, qty, tp_pct, outside)

                await asyncio.sleep(check_sec)
            except Exception as e:
                logging.exception(f"DCA cycle error: {e}")
                await asyncio.sleep(2)

    async def start(self, notify_cb: Callable[[str], asyncio.Future] | None = None):
        if self.task and not self.task.done():
            return
        self.notify = notify_cb
        self.running.set()
        async def _runner():
            try:
                await self.cycle()
            finally:
                await self.disconnect()
        self.task = asyncio.create_task(_runner(), name="dca_engine")

    async def pause(self):
        self.running.clear()

    async def stop(self):
        self.running.clear()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
        self.task = None
