from __future__ import annotations
import os
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.exec.order_router import OrderRouter
from app.paper.engine import PaperEngine

app = FastAPI(title="Trading Bot API", version=os.getenv("APP_VERSION", "0.3.0"))

MODE = os.getenv("MODE", "paper").lower()
router = OrderRouter(mode="paper" if MODE not in ("paper", "live") else MODE)
paper = PaperEngine(router)

# ---------- Models ----------
class MarketOrderIn(BaseModel):
    symbol: str
    side: str  # "buy" | "sell"
    quantity: float

class LimitOrderIn(MarketOrderIn):
    price: float

class OCOIn(MarketOrderIn):
    price: float
    stop_price: float
    stop_limit_price: float

class PaperStartIn(BaseModel):
    symbol: str = "BTCUSDT"
    interval_s: float = 1.0
    threshold_bps: float = 0.2
    trade_qty: float = 0.001

class RiskSetIn(BaseModel):
    capital_base_usd: Optional[float] = None
    risk_per_trade_bps: Optional[float] = None
    daily_loss_cap_bps: Optional[float] = None
    max_drawdown_bps: Optional[float] = None
    max_position_usd: Optional[float] = None
    allow_leverage: Optional[bool] = None
    max_leverage: Optional[float] = None

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
    # stub speichern
    o = await router.place_oco_stub(body.symbol, body.side, body.quantity, body.price, body.stop_price, body.stop_limit_price)
    return {"ok": True, "oco_id": o.id, "note": "OCO stub gespeichert (keine Live-Logik in Step 3/2.1)"}

@app.get("/orders")
async def list_orders():
    return {"ok": True, "orders": [o.dict() for o in router.list_orders()]}

# ---------- Paper loop ----------
@app.post("/paper/start")
async def paper_start(body: PaperStartIn):
    return await paper.start(body.symbol, body.interval_s, body.threshold_bps, body.trade_qty)

@app.post("/paper/stop")
async def paper_stop():
    return await paper.stop()

@app.get("/paper/status")
async def paper_status():
    return paper.status()

@app.post("/paper/reset")
async def paper_reset():
    return paper.reset_accounting()

# ---------- Risk ----------
@app.get("/risk/config")
async def risk_get():
    return {"ok": True, "risk": paper.risk.__dict__}

@app.post("/risk/config")
async def risk_set(body: RiskSetIn):
    payload = {k: v for k, v in body.dict().items() if v is not None}
    return paper.set_risk(payload)
