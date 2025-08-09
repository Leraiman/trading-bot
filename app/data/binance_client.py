import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Dict, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

log = logging.getLogger(__name__)

def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)

def _now_ms() -> int:
    return int(time.time() * 1000)

@dataclass
class BinanceConfig:
    market: str = _env_str("MARKET", "spot")
    symbols: List[str] = tuple(_env_str("SYMBOLS", "BTCUSDT").split(","))
    interval: str = _env_str("INTERVAL", "1h")
    fee_bps: float = float(os.getenv("FEE_BPS", "10"))
    slippage_bps: float = float(os.getenv("SLIPPAGE_BPS", "5"))
    latency_ms: int = int(os.getenv("LATENCY_MS", "150"))
    ro_key: str = _env_str("BINANCE_READONLY_KEY", "")
    ro_secret: str = _env_str("BINANCE_READONLY_SECRET", "")
    trade_key: str = _env_str("BINANCE_TRADE_KEY", "")
    trade_secret: str = _env_str("BINANCE_TRADE_SECRET", "")
    base_rest: str = "https://api.binance.com"
    base_ws: str = "wss://stream.binance.com:9443"

class BinanceClient:
    def __init__(self, cfg: Optional[BinanceConfig] = None) -> None:
        self.cfg = cfg or BinanceConfig()
        self._ac: Optional[httpx.AsyncClient] = None
        self.time_offset_ms: int = 0
        self._ws_tasks: list[asyncio.Task] = []

    async def __aenter__(self) -> "BinanceClient":
        if self._ac is None:
            headers = {"X-MBX-APIKEY": self.cfg.ro_key} if self.cfg.ro_key else {}
            self._ac = httpx.AsyncClient(
                base_url=self.cfg.base_rest,
                headers=headers,
                timeout=httpx.Timeout(10.0, read=20.0),
            )
        await self.sync_time()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        for t in self._ws_tasks:
            t.cancel()
        if self._ac:
            await self._ac.aclose()
            self._ac = None

    @retry(stop=stop_after_attempt(5), wait=wait_exponential_jitter(0.5, 3.0))
    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        assert self._ac is not None, "AsyncClient not initialized"
        r = await self._ac.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def server_time(self) -> int:
        data = await self._get("/api/v3/time")
        return int(data["serverTime"])

    async def sync_time(self) -> None:
        t0 = _now_ms()
        s = await self.server_time()
        t1 = _now_ms()
        local_est = (t0 + t1) // 2
        self.time_offset_ms = s - local_est
        log.info("binance_time_sync", extra={"server_ms": s, "local_ms": local_est, "offset_ms": self.time_offset_ms})

    async def klines(self, symbol: str, interval: str, limit: int = 100,
                     start_time_ms: Optional[int] = None, end_time_ms: Optional[int] = None) -> List[List[Any]]:
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(max(limit, 1), 1000),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms + self.time_offset_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms + self.time_offset_ms
        return await self._get("/api/v3/klines", params=params)

    async def stream_klines(self, symbol: str, interval: str,
                            queue: "asyncio.Queue[Dict[str, Any]]", stop_event: asyncio.Event) -> None:
        import websockets
        stream = f"{symbol.lower()}@kline_{interval}"
        url = f"{self.cfg.base_ws}/ws/{stream}"
        backoff = 1.0
        while not stop_event.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    log.info("ws_connected", extra={"stream": stream})
                    backoff = 1.0
                    while not stop_event.is_set():
                        msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                        queue.put_nowait(json.loads(msg))
            except asyncio.TimeoutError:
                log.warning("ws_timeout_reconnect", extra={"stream": stream})
            except Exception as e:
                log.error("ws_error", extra={"error": str(e), "stream": stream})
            await asyncio.sleep(min(backoff, 20.0))
            backoff *= 1.7

    def start_ws_task(self, symbol: str, interval: str,
                      queue: "asyncio.Queue[Dict[str, Any]]", stop_event: asyncio.Event) -> asyncio.Task:
        task = asyncio.create_task(self.stream_klines(symbol, interval, queue, stop_event))
        self._ws_tasks.append(task)
        return task
