import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.exec.risk_engine import RiskEngine

log = logging.getLogger(__name__)

try:
    from binance.spot import Spot as SpotClient  # type: ignore
except Exception:
    SpotClient = None  # type: ignore

def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "y", "on")

def _now_ms() -> int:
    return int(time.time() * 1000)

@dataclass
class OrderResult:
    ok: bool
    client_order_id: str
    exchange_order_id: Optional[str]
    raw: Dict[str, Any]

class OrderRouter:
    def __init__(self, risk: RiskEngine) -> None:
        self.risk = risk
        self.enable_live = _env_bool("ENABLE_LIVE_ORDERS", False)
        self.lock = threading.Lock()
        self.cache: Dict[str, OrderResult] = {}
        self._spot = None
        if self.enable_live:
            api_key = os.getenv("BINANCE_TRADE_KEY", "")
            api_secret = os.getenv("BINANCE_TRADE_SECRET", "")
            if not api_key or not api_secret:
                raise RuntimeError("ENABLE_LIVE_ORDERS=true aber Trade-Keys fehlen.")
            if SpotClient is None:
                raise RuntimeError("binance-connector nicht installiert.")
            self._spot = SpotClient(key=api_key, secret=api_secret)

    @staticmethod
    def make_client_order_id(symbol: str, side: str, qty: str, idem_key: str) -> str:
        base = f"{symbol}|{side}|{qty}|{idem_key}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:24]

    def place_market_quote(self, symbol: str, side: str, quote_qty_usd: float,
                           idem_key: str, est_risk_usd: Optional[float] = None) -> OrderResult:
        qty_str = f"{quote_qty_usd:.2f}"
        coid = self.make_client_order_id(symbol, side, qty_str, idem_key)
        with self.lock:
            if coid in self.cache:
                log.info("idempotent_hit", extra={"client_order_id": coid})
                return self.cache[coid]
            risk_amt = est_risk_usd if est_risk_usd is not None else self.risk.allowed_risk_per_trade_usd()
            ok, reason = self.risk.pre_trade_check(risk_amt)
            if not ok:
                res = OrderResult(ok=False, client_order_id=coid, exchange_order_id=None, raw={"reason": reason})
                self.cache[coid] = res
                return res
            if not self.enable_live or self._spot is None:
                res = OrderResult(
                    ok=True, client_order_id=coid, exchange_order_id=None,
                    raw={"dry_run": True, "symbol": symbol, "side": side, "type": "MARKET",
                         "quoteOrderQty": qty_str, "ts": _now_ms()}
                )
                self.cache[coid] = res
                return res
            try:
                resp = self._spot.new_order(
                    symbol=symbol.upper(), side=side, type="MARKET",
                    quoteOrderQty=qty_str, newClientOrderId=coid,
                )
                res = OrderResult(ok=True, client_order_id=coid,
                                  exchange_order_id=str(resp.get("orderId")), raw=resp)
                self.cache[coid] = res
                return res
            except Exception as e:
                log.exception("place_market_quote_error")
                res = OrderResult(ok=False, client_order_id=coid, exchange_order_id=None, raw={"error": str(e)})
                self.cache[coid] = res
                return res

    def get_order(self, symbol: str, client_order_id: str) -> Dict[str, Any]:
        if not self.enable_live or self._spot is None:
            return {"dry_run": True, "clientOrderId": client_order_id, "status": "SIMULATED"}
        try:
            return self._spot.get_order(symbol=symbol.upper(), origClientOrderId=client_order_id)
        except Exception as e:
            return {"error": str(e), "clientOrderId": client_order_id}

    def open_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        if not self.enable_live or self._spot is None:
            return {"dry_run": True, "orders": []}
        try:
            if symbol:
                return {"orders": self._spot.get_open_orders(symbol=symbol.upper())}
            return {"orders": self._spot.get_open_orders()}
        except Exception as e:
            return {"error": str(e)}
