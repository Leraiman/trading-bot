from __future__ import annotations
import asyncio
from typing import Optional
import httpx

BINANCE_BASE = "https://api.binance.com"

class PriceFeed:
    def __init__(self, symbol: str, interval_s: float = 2.0):
        # symbol z.B. "BTCUSDT" (Binance verlangt Großschreibung)
        self.symbol = symbol.upper()
        self.interval_s = max(1.5, float(interval_s))
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.last_price: Optional[float] = None
        self.last_ts_ms: int = 0

    async def _poll(self):
        headers = {"User-Agent": "trading-bot/0.3"}
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            endpoint = f"{BINANCE_BASE}/api/v3/ticker/price"
            params = {"symbol": self.symbol}
            while not self._stop.is_set():
                try:
                    r = await client.get(endpoint, params=params)
                    r.raise_for_status()
                    data = r.json()
                    self.last_price = float(data["price"])
                    self.last_ts_ms = int(asyncio.get_event_loop().time() * 1000)
                except Exception as e:
                    # sanft weiterprobieren
                    # (Optional: Logging)
                    pass
                await asyncio.sleep(self.interval_s)

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._poll())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=self.interval_s + 2.0)
            except asyncio.TimeoutError:
                pass
            self._task = None
