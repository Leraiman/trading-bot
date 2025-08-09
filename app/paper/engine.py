from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from app.data.price_feed import PriceFeed
from app.exec.order_router import OrderRouter
from app.paper.risk import RiskConfig


@dataclass
class PaperState:
    running: bool = False
    symbol: str = "BTCUSDT"
    interval_s: float = 1.0
    threshold_bps: float = 0.2
    trade_qty: float = 0.001

    # accounting
    last_price: Optional[float] = None
    last_ts_ms: int = 0
    position_qty: float = 0.0
    cash_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    equity_usd: float = 0.0
    high_watermark_usd: float = 0.0
    cum_day_loss_usd: float = 0.0
    start_equity_usd: float = 0.0


class PaperEngine:
    def __init__(self, router: OrderRouter):
        self.router = router
        self.feed = PriceFeed()
        self.state = PaperState()
        self.risk = RiskConfig()
        self._task: Optional[asyncio.Task] = None

    # ---------- helpers ----------
    def _update_equity(self, px: Optional[float]):
        if px is None:
            return
        inventory = self.state.position_qty * px
        equity = self.state.cash_usd + self.state.realized_pnl_usd + inventory
        self.state.equity_usd = equity
        if self.state.start_equity_usd == 0:
            self.state.start_equity_usd = equity
            self.state.high_watermark_usd = equity
        self.state.high_watermark_usd = max(self.state.high_watermark_usd, equity)

    def _risk_blocked(self, next_side: Optional[str], px: Optional[float]) -> Optional[str]:
        """Return reason if trading must be blocked, else None."""
        if px is None:
            return "no_price"

        start_eq = self.state.start_equity_usd or self.risk.capital_base_usd
        dd = self.state.high_watermark_usd - self.state.equity_usd
        if dd >= self.risk.max_drawdown_usd():
            return "max_drawdown"

        if self.state.cum_day_loss_usd <= -self.risk.daily_loss_cap_usd():
            return "daily_loss_cap"

        # Max Position USD
        next_pos = self.state.position_qty
        if next_side == "buy":
            next_pos += self.state.trade_qty
        elif next_side == "sell":
            next_pos -= self.state.trade_qty
        if abs(next_pos * px) > self.risk.max_position_usd:
            return "max_position"

        # Leverage (simple: abs(position)*px <= max_leverage*equity)
        if not self.risk.allow_leverage:
            if self.state.equity_usd > 0 and abs(next_pos * px) > self.state.equity_usd * self.risk.max_leverage:
                return "leverage"

        return None

    # ---------- API ----------
    async def start(self, symbol: str, interval_s: float, threshold_bps: float, trade_qty: float):
        if self._task and not self._task.done():
            await self.stop()

        self.state = PaperState(
            running=True,
            symbol=symbol,
            interval_s=float(interval_s),
            threshold_bps=float(threshold_bps),
            trade_qty=float(trade_qty),
            last_price=None,
            last_ts_ms=0,
            position_qty=self.state.position_qty,   # preserve between runs
            cash_usd=self.state.cash_usd,
            realized_pnl_usd=self.state.realized_pnl_usd,
            equity_usd=self.state.equity_usd,
            high_watermark_usd=self.state.high_watermark_usd or self.risk.capital_base_usd,
            cum_day_loss_usd=self.state.cum_day_loss_usd,
            start_equity_usd=self.state.start_equity_usd or self.risk.capital_base_usd,
        )

        self._task = asyncio.create_task(self._loop())
        return self.status()

    async def stop(self):
        self.state.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {"ok": True, "state": asdict(self.state), "risk": self.risk.__dict__}

    def set_risk(self, payload: Dict[str, Any]):
        for k, v in payload.items():
            if hasattr(self.risk, k):
                setattr(self.risk, k, v)
        return {"ok": True, "risk": self.risk.__dict__}

    def reset_accounting(self):
        self.state.cash_usd = 0.0
        self.state.realized_pnl_usd = 0.0
        self.state.position_qty = 0.0
        self.state.start_equity_usd = 0.0
        self.state.high_watermark_usd = 0.0
        self.state.cum_day_loss_usd = 0.0
        self.state.equity_usd = 0.0
        return self.status()

    # ---------- core loop ----------
    async def _loop(self):
        px_prev: Optional[float] = None

        while self.state.running:
            t0 = time.time()
            px = await self.feed.get_price(self.state.symbol)
            now_ms = int(time.time() * 1000)

            self.state.last_price = px
            self.state.last_ts_ms = now_ms
            self._update_equity(px)

            if px is not None and px_prev is not None:
                move_bps = (px - px_prev) / px_prev * 10_000.0
                if abs(move_bps) >= self.state.threshold_bps:
                    side = "sell" if move_bps > 0 else "buy"
                    reason = self._risk_blocked(side, px)
                    if reason is None:
                        o = await self.router.place_market(self.state.symbol, side, self.state.trade_qty)
                        fill_px = o.avg_fill_price or px

                        # accounting: cash/position & realized pnl
                        if side == "buy":
                            # spend cash, increase position
                            self.state.cash_usd -= fill_px * self.state.trade_qty
                            self.state.position_qty += self.state.trade_qty
                        else:
                            # sell inventory; realized pnl on fraction
                            qty = self.state.trade_qty
                            realized = fill_px * qty  # add proceeds
                            self.state.cash_usd += realized
                            self.state.position_qty -= qty
                            # PnL vs. notional is implicit via inventory marking

                        self._update_equity(fill_px)

                        # update cum day loss vs start equity
                        dd_today = self.state.equity_usd - (self.state.start_equity_usd or self.risk.capital_base_usd)
                        self.state.cum_day_loss_usd = min(self.state.cum_day_loss_usd, dd_today)

                    else:
                        # blocked -> just skip trading this tick
                        pass

            px_prev = px
            # sleep remaining
            dt = self.state.interval_s - (time.time() - t0)
            if dt > 0:
                await asyncio.sleep(dt)
