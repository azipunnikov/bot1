# db.py
import json
import aiosqlite
from typing import Dict, Any

DB_PATH = "bot.db"  # или путь к твоей базе

def _bool_to_int(v: Any) -> int | None:
    if v is None:
        return None
    return 1 if bool(v) else 0

async def upsert_order(info: Dict[str, Any]) -> None:
    """
    Сохраняет/обновляет запись об ордере по permId.
    Ожидает словарь вида:
    {
      'orderId': 1756783688, 'permId': 1180284306, 'action': 'SELL', 'symbol': 'AAPL',
      'orderType': 'LMT', 'lmtPrice': 235.0, 'tif': 'GTC', 'outsideRth': True,
      'status': 'Submitted', 'filled': 0.0, 'remaining': 1.0, 'avgFillPrice': 0.0,
      'whyHeld': '', 'lastFillPrice': 0.0
    }
    """
    # обязательное поле
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
    set_clause = ", ".join([f"{k}=excluded.{k}" for k in payload.keys() if k not in ("permId",)])

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
