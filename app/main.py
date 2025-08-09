import os
import time
import asyncio
import logging
from typing import Optional, List

import uvloop  # type: ignore
uvloop.install()

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.util.logging import setup_logging
from app.exec.risk_engine import RiskEngine

# ---- Logging ----
setup_logging()
log = logging.getLogger("app")

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
APP_START = time.time()

# ---- Metrics ----
REQ_COUNT = Counter("http_requests_total", "Total HTTP requests", ["path", "method"])
REQ_LATENCY = Histogram("http_request_latency_seconds", "Request latency", ["path", "method"])
UPTIME_GAUGE = Gauge("process_uptime_seconds", "Process uptime in seconds")

# ---- App ----
app = FastAPI(title="Trading Bot API", version=APP_VERSION)

# Global state (skeleton)
risk = RiskEngine()
_live_lock = asyncio.Lock()
_live_running = False
_live_flat = False
_current_position_usd = 0.0

# ---- Models ----
class StartConfig(BaseModel):
    symbols: List[str] = Field(default_factory=lambda: os.getenv("SYMBOLS", "BTCUSDT").split(","))
    interval: str = Field(default=os.getenv("INTERVAL", "1h"))
    capital_base_usd: float = Field(default=float(os.getenv("CAPITAL_BASE_USD", "10000")))
    mode: str = Field(default="paper")  # "paper" or "live"

class Status(BaseModel):
    ok: bool
    version: str
    uptime_s: float

# ---- Middleware-lite (manual metrics) ----
@app.middleware("http")
async def _metrics_mw(request, call_next):
    start = time.time()
    path = request.url.path
    method = request.method
    REQ_COUNT.labels(path=path, method=method).inc()
    try:
        resp = await call_next(request)
        return resp
    finally:
        REQ_LATENCY.labels(path=path, method=method).observe(time.time() - start)

# ---- Endpoints ----
@app.get("/status", response_model=Status)
async def status():
    uptime = time.time() - APP_START
    UPTIME_GAUGE.set(uptime)
    return Status(ok=True, version=APP_VERSION, uptime_s=uptime)

@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/risk/summary")
async def risk_summary():
    return JSONResponse(risk.summary())

# ---- Paper orchestrator (skeleton) ----
@app.post("/paper/start")
async def paper_start(cfg: StartConfig, bg: BackgroundTasks):
    log.info("paper_start", extra=cfg.model_dump())
    # In Step 2 this will kick off a paper broker loop
    return {"ok": True, "mode": "paper", "msg": "Paper run started (skeleton)."}

@app.post("/paper/stop")
async def paper_stop():
    log.info("paper_stop")
    return {"ok": True, "mode": "paper", "msg": "Paper run stopped (skeleton)."}

# ---- Live orchestrator (skeleton) ----
@app.post("/live/start")
async def live_start(cfg: StartConfig, bg: BackgroundTasks):
    global _live_running, _live_flat
    async with _live_lock:
        if _live_running:
            return {"ok": True, "mode": "live", "msg": "Already running."}
        if risk.state.kill_switch or risk.state.trading_halted:
            raise HTTPException(status_code=423, detail="KillSwitch or Trading Halt active")

        _live_running = True
        _live_flat = False
        log.warning("live_start", extra={"cfg": cfg.model_dump()})
        # Step 2: start background consume loop (WS), route orders etc.
        return {"ok": True, "mode": "live", "msg": "Live run started (skeleton)."}

@app.post("/live/stop")
async def live_stop():
    global _live_running
    async with _live_lock:
        if not _live_running:
            return {"ok": True, "mode": "live", "msg": "Not running."}
        _live_running = False
        log.warning("live_stop")
        return {"ok": True, "mode": "live", "msg": "Live run stopped (skeleton)."}

@app.post("/live/flat")
async def live_flat():
    """
    Kill‑Switch: Sofortige Flat‑Schaltung (State‑Level).
    In Step 2 werden hier offene Orders gecancelt / Positionen geschlossen.
    """
    global _live_flat, _live_running
    risk.set_kill_switch(True, reason="api_flat")
    _live_flat = True
    _live_running = False
    log.error("live_flat_triggered")
    return {"ok": True, "mode": "live", "msg": "KillSwitch engaged. Flatten requested."}

@app.get("/live/position")
async def live_position():
    return {
        "ok": True,
        "running": _live_running,
        "flat": _live_flat,
        "position_usd": _current_position_usd,
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=bool(os.getenv("APP_ENV") == "dev"))
