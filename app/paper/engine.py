from __future__ import annotations
import asyncio
import time
import uuid
from typing import Optional, Dict, Any

from app.exec.order_router import OrderRouter
from app.data.price_feed import PriceFeed

class PaperEngine:
    def __init__(self, router: OrderRouter):
        self.router = router
        self._state: Dict[str, Any] = {
            "running": False,
            "symbol": None,
            "interval_s": 2.0,
            "threshold_bps": 15.0,
            "trade_qty": 0.001,
            "last_price": None,
            "position_qty": 0.0,
            "cash_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "last_ts_ms": 0,
            "equity_usd": 0.0,
        }
        self._loop_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._feed: Optional[PriceFeed] = None
        self._anchor_price: Optional[float] = None  # Referenz für BPS‑Berechnung

    async def start(self, symbol: str = "BTCUSDT", interval_s: float = 2.0,
                    threshold_bps: float = 15.0, trade_qty: float = 0.001):
        if self._state["running"]:
            return {"ok": True, "note": "already running", "state": self._state}

        self._state.update({
            "running": True,
            "symbol": symbol.upper(),
            "interval_s": float(interval_s),
            "threshold_bps": float(threshold_bps),
            "trade_qty": float(trade_qty),
        })

        # Price‑Feed starten
        self._feed = PriceFeed(self._state["symbol"], self._state["interval_s"])
        await self._feed.start()

        self._stop.clear()
        self._loop_task = asyncio.create_task(self._run())
        return {"ok": True, "state": self._state}

    async def stop(self):
        self._state["running"] = False
        self._stop.set()
        if self._loop_task:
            try:
                await asyncio.wait_for(self._loop_task, timeout=self._state["interval_s"] + 2.0)
            except asyncio.TimeoutError:
                pass
            self._loop_task = None
        if self._feed:
            await self._feed.stop()
            self._feed = None
        return {"ok": True, "state": self._state}

    async def status(self):
        return {"ok": True, "state": self._state}

    async def _run(self):
        # Warte bis der Feed den ersten Preis hat
        for _ in range(50):
            if self._feed and self._feed.last_price:
                break
            await asyncio.sleep(0.1)

        if self._feed and self._feed.last_price:
            self._anchor_price = self._feed.last_price

        try:
            while not self._stop.is_set():
                if not self._feed or self._feed.last_price is None:
                    await asyncio.sleep(self._state["interval_s"])
                    continue

                p = float(self._feed.last_price)
                self._state["last_price"] = p
                self._state["last_ts_ms"] = int(time.time() * 1000)

                if self._anchor_price is None:
                    self._anchor_price = p

                # Abweichung in Basispunkten
                bps = (p / self._anchor_price - 1.0) * 10000.0

                # simple mean‑reversion: bei +threshold_bps -> SELL; bei -threshold_bps -> BUY
                try:
                    if bps >= self._state["threshold_bps"]:
                        await self._trade("sell", self._state["trade_qty"], p)
                        self._anchor_price = p  # Anker neu setzen
                    elif bps <= -self._state["threshold_bps"]:
                        await self._trade("buy", self._state["trade_qty"], p)
                        self._anchor_price = p
                except Exception:
                    # still weiterlaufen
                    pass

                # Equity schätzen (Mark‑to‑Market)
                pos = float(self._state["position_qty"])
                cash = float(self._state["cash_usd"])
                self._state["equity_usd"] = cash + pos * p

                await asyncio.sleep(self._state["interval_s"])
        finally:
            self._state["running"] = False

    async def _trade(self, side: str, qty: float, price: float):
        # Paper: Market‑Order via Router, dann einfache PnL‑Buchung
        o = await self.router.place_market(self._state["symbol"], side, qty)
        fill = o.avg_fill_price or price
        if side == "buy":
            self._state["position_qty"] += qty
            self._state["cash_usd"] -= qty * fill
        else:
            self._state["position_qty"] -= qty
            self._state["cash_usd"] += qty * fill
        # realized_pnl lassen wir hier null; könnte man bei Positions‑Schließung addieren
        return o
