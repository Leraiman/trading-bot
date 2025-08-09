from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, asdict
from typing import Optional

from app.exec.order_router import OrderRouter


@dataclass
class PaperState:
    running: bool = False
    symbol: str = "BTCUSDT"
    interval_s: float = 2.0
    threshold_bps: float = 15.0   # 15 bps = 0.15%
    trade_qty: float = 0.001
    last_price: Optional[float] = None
    position_qty: float = 0.0
    cash_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    last_ts_ms: int = 0

    def dict(self):
        d = asdict(self)
        d["equity_usd"] = self.cash_usd + (self.position_qty * (self.last_price or 0.0))
        return d


class PaperEngine:
    def __init__(self, router: OrderRouter) -> None:
        self.router = router
        self.state = PaperState()
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self, symbol: str = "BTCUSDT", interval_s: float = 2.0,
                    threshold_bps: float = 15.0, trade_qty: float = 0.001) -> dict:
        async with self._lock:
            if self._task and not self._task.done():
                return {"ok": True, "note": "already running", "state": self.state.dict()}

            self.state = PaperState(
                running=True, symbol=symbol, interval_s=interval_s,
                threshold_bps=threshold_bps, trade_qty=trade_qty
            )
            self._task = asyncio.create_task(self._run())
            return {"ok": True, "state": self.state.dict()}

    async def stop(self) -> dict:
        async with self._lock:
            if self._task:
                self._task.cancel()
            self.state.running = False
            return {"ok": True, "state": self.state.dict()}

    async def status(self) -> dict:
        return {"ok": True, "state": self.state.dict()}

    # ---- core loop ----
    async def _run(self):
        try:
            while True:
                px = await self.router._get_last_price(self.state.symbol)
                now = int(time.time() * 1000)

                # Erstes Sample?
                if self.state.last_price is None:
                    self.state.last_price = px
                    self.state.last_ts_ms = now
                    await asyncio.sleep(self.state.interval_s)
                    continue

                ret = (px - self.state.last_price) / self.state.last_price
                bps = ret * 10_000

                # simple mean‑reverting Regel: fällt > threshold → BUY, steigt > threshold → SELL
                try:
                    if bps <= -self.state.threshold_bps:
                        # BUY
                        o = await self.router.place_market(self.state.symbol, "buy", self.state.trade_qty)
                        self.state.position_qty += o.filled_quantity
                        self.state.cash_usd -= (o.avg_fill_price or 0.0) * o.filled_quantity
                    elif bps >= self.state.threshold_bps:
                        # SELL
                        o = await self.router.place_market(self.state.symbol, "sell", self.state.trade_qty)
                        self.state.position_qty -= o.filled_quantity
                        self.state.cash_usd += (o.avg_fill_price or 0.0) * o.filled_quantity
                except Exception:
                    # Fehler im Router ignorieren, Loop weiter
                    pass

                # Realized PnL (vereinfachte Sicht: nur bei Flat wird PnL realisiert)
                if abs(self.state.position_qty) < 1e-9:
                    self.state.realized_pnl_usd = self.state.cash_usd

                self.state.last_price = px
                self.state.last_ts_ms = now
                await asyncio.sleep(self.state.interval_s)
        except asyncio.CancelledError:
            pass
        finally:
            self.state.running = False
