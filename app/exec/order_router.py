from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Dict, Literal, Optional, List

import httpx


Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "oco"]
OrderStatus = Literal["new", "filled", "partially_filled", "rejected", "canceled"]


@dataclass
class Order:
    id: str
    symbol: str
    side: Side
    type: OrderType
    quantity: float
    price: Optional[float] = None
    status: OrderStatus = "new"
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    ts_ms: int = 0
    note: Optional[str] = None

    def dict(self) -> dict:
        return asdict(self)


class OrderRouter:
    """
    Minimaler Router.
    - mode='paper' -> füllt Market sofort zum aktuellen Preis (REST Ticker).
    - Limit-Orders werden 'filled', wenn price erreicht ist (vereinfachte Logik, sofortige Prüfung).
    - OCO -> Stub (noch ohne echte Logik).
    """
    def __init__(self, mode: Literal["paper", "live"] = "paper") -> None:
        self.mode = mode
        self._orders: Dict[str, Order] = {}
        self._lock = asyncio.Lock()

    # ---------- Public API ----------
    async def place_market(self, symbol: str, side: Side, quantity: float) -> Order:
        symbol = self._normalize_symbol(symbol)
        now = int(time.time() * 1000)

        # Preis holen
        px = await self._get_last_price(symbol)

        order = Order(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            type="market",
            quantity=quantity,
            status="filled" if self.mode == "paper" else "new",
            filled_quantity=quantity if self.mode == "paper" else 0.0,
            avg_fill_price=px if self.mode == "paper" else None,
            ts_ms=now,
            note="paper fill" if self.mode == "paper" else None,
        )
        async with self._lock:
            self._orders[order.id] = order
        return order

    async def place_limit(self, symbol: str, side: Side, quantity: float, price: float) -> Order:
        symbol = self._normalize_symbol(symbol)
        now = int(time.time() * 1000)

        order = Order(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            type="limit",
            quantity=quantity,
            price=price,
            status="new",
            ts_ms=now,
        )

        # vereinfachte Sofort-Prüfung: wenn limit 'in the money', sofort füllen
        last = await self._get_last_price(symbol)
        should_fill = (side == "buy" and last <= price) or (side == "sell" and last >= price)
        if self.mode == "paper" and should_fill:
            order.status = "filled"
            order.filled_quantity = quantity
            order.avg_fill_price = last
            order.note = "paper limit immediate fill"

        async with self._lock:
            self._orders[order.id] = order
        return order

    async def place_oco_stub(self, symbol: str, side: Side, quantity: float,
                             price: float, stop_price: float, stop_limit_price: float) -> dict:
        symbol = self._normalize_symbol(symbol)
        oco_id = str(uuid.uuid4())
        note = "OCO stub gespeichert (keine Live-Logik in Step 3/2.1)"
        async with self._lock:
            self._orders[oco_id] = Order(
                id=oco_id, symbol=symbol, side=side, type="oco",
                quantity=quantity, price=price, status="new", ts_ms=int(time.time()*1000),
                note=note
            )
        return {"oco_id": oco_id, "note": note}

    async def get_order(self, order_id: str) -> Optional[Order]:
        async with self._lock:
            return self._orders.get(order_id)

    async def list_orders(self) -> List[Order]:
        async with self._lock:
            return list(self._orders.values())

    # ---------- Helpers ----------
    async def _get_last_price(self, symbol: str) -> float:
        # nutzt Binance REST /api/v3/ticker/price
        url = "https://api.binance.com/api/v3/ticker/price"
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, params={"symbol": symbol})
            r.raise_for_status()
            px = float(r.json()["price"])
            return px

    def _normalize_symbol(self, symbol: str) -> str:
        """BTCUSDT, ethusdt, BTC-USDT, BTC/USDT -> BTCUSDT"""
        s = symbol.upper().replace("-", "").replace("/", "")
        return s
