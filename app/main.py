from __future__ import annotations

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.exec.order_router import OrderRouter
from app.paper.engine import PaperEngine

app = FastAPI(title="Trading Bot API", version=os.getenv("APP_VERSION", "0.3.0"))

# Router / Engine
MODE = os.getenv("MODE", "paper").lower()
router = OrderRouter(mode="paper" if MODE not in ("paper", "live") else MODE)
paper = PaperEngine(router)

# ---------- Models ----------
class MarketOrderIn(BaseModel):
    symbol: str
    side: str      # "buy" | "sell"
    quantity: float

class LimitOrderIn(MarketOrderIn):
    price: float

class OCOIn(MarketOrderIn):
    price: float
    stop_price: float
    stop_limit_price: float

# ---------- Health ----------
@app.get("/status")
async def status():
    return {"ok": True, "version": app.version, "mode": MODE}

# ---------- Orders ----------
@app.post("/orders/market")
async def create_market_order(body: MarketOrderIn):
    side = body.side.lower()
    if side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'")
    o = await router.place_market(body.symbol, side, body.quantity)
    return {"ok": True, "order": o.dict()}

@app.post("/orders/limit")
async def create_limit_order(body: LimitOrderIn):
    side = body.side.lower()
    if side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'")
    o = await router.place_limit(body.symbol, side, body.quantity, body.price)
    return {"ok": True, "order": o.dict()}

@app.post("/orders/oco")
async def create_oco_order(body: OCOIn):
    side = body.side.lower()
    if side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'")
    res = await router.place_oco_stub(
        body.symbol, side, body.quantity, body.price, body.stop_price, body.stop_limit_price
    )
    return {"ok": True, **res}

@app.get("/orders")
async def list_orders():
    items = [o.dict() for o in await router.list_orders()]
    return {"ok": True, "orders": items}

@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    o = await router.get_order(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="order not found")
    return {"ok": True, "order": o.dict()}

# ---------- Paper Loop ----------
class PaperStartIn(BaseModel):
    symbol: str = "BTCUSDT"
    interval_s: float = 2.0
    threshold_bps: float = 15.0
    trade_qty: float = 0.001

@app.post("/paper/start")
async def paper_start(body: PaperStartIn):
    return await paper.start(
        symbol=body.symbol,
        interval_s=body.interval_s,
        threshold_bps=body.threshold_bps,
        trade_qty=body.trade_qty,
    )

@app.post("/paper/stop")
async def paper_stop():
    return await paper.stop()

@app.get("/paper/status")
async def paper_status():
    return await paper.status()
