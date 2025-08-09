import os
import time
import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any

logger = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "y", "on")

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default

@dataclass
class RiskParams:
    capital_base_usd: float
    risk_per_trade_bps: float
    daily_loss_cap_bps: float
    max_drawdown_bps: float
    allow_leverage: bool
    max_leverage: float

@dataclass
class RiskState:
    equity_start_usd: float
    equity_usd: float
    day_start_ts: float
    day_start_equity_usd: float
    realized_pnl_today_usd: float
    max_equity_usd: float
    min_equity_usd: float
    trading_halted: bool
    kill_switch: bool
    halt_reason: str

class RiskEngine:
    """
    Risk engine with:
      - Max risk per trade (bps of equity)
      - Daily loss cap (bps of day start equity)
      - Max drawdown (bps of equity start)
      - Kill switch (external trigger)
    """
    def __init__(self) -> None:
        self.params = RiskParams(
            capital_base_usd=_env_float("CAPITAL_BASE_USD", 10_000.0),
            risk_per_trade_bps=_env_float("RISK_PER_TRADE_BPS", 50.0),
            daily_loss_cap_bps=_env_float("DAILY_LOSS_CAP_BPS", 200.0),
            max_drawdown_bps=_env_float("MAX_DRAWDOWN_BPS", 1000.0),
            allow_leverage=_env_bool("ALLOW_LEVERAGE", False),
            max_leverage=_env_float("MAX_LEVERAGE", 1.0),
        )
        now = time.time()
        capital = self.params.capital_base_usd
        self.state = RiskState(
            equity_start_usd=capital,
            equity_usd=capital,
            day_start_ts=now,
            day_start_equity_usd=capital,
            realized_pnl_today_usd=0.0,
            max_equity_usd=capital,
            min_equity_usd=capital,
            trading_halted=False,
            kill_switch=_env_bool("KILL_SWITCH", False),
            halt_reason="",
        )
        logger.info("risk_engine_init", extra={"params": asdict(self.params)})

    # ===== Helpers =====

    def _daily_loss_limit_usd(self) -> float:
        return self.state.day_start_equity_usd * (self.params.daily_loss_cap_bps / 10_000.0)

    def _max_drawdown_limit_usd(self) -> float:
        return self.state.equity_start_usd * (self.params.max_drawdown_bps / 10_000.0)

    def allowed_risk_per_trade_usd(self) -> float:
        return self.state.equity_usd * (self.params.risk_per_trade_bps / 10_000.0)

    # ===== External interface =====

    def pre_trade_check(self, est_risk_usd: float) -> tuple[bool, str]:
        """
        est_risk_usd: expected worst-case loss (e.g., distance to SL * position size)
        """
        if self.state.kill_switch:
            return False, "KillSwitch active"

        if self.state.trading_halted:
            return False, f"Trading halted: {self.state.halt_reason}"

        limit = self.allowed_risk_per_trade_usd()
        if est_risk_usd > limit + 1e-9:
            return False, f"Risk per trade exceeded: {est_risk_usd:.2f} > {limit:.2f} USD"

        # Daily loss cap check (pre emptive)
        if self.state.realized_pnl_today_usd < -self._daily_loss_limit_usd():
            return False, "Daily loss cap breached"

        # Drawdown check (pre emptive)
        dd = self.state.equity_start_usd - self.state.equity_usd
        if dd > self._max_drawdown_limit_usd():
            return False, "Max drawdown breached"

        return True, "OK"

    def record_fill_pnl(self, realized_pnl_usd: float) -> None:
        # Update equity & daily PnL
        self.state.equity_usd += realized_pnl_usd
        self.state.realized_pnl_today_usd += realized_pnl_usd
        self.state.max_equity_usd = max(self.state.max_equity_usd, self.state.equity_usd)
        self.state.min_equity_usd = min(self.state.min_equity_usd, self.state.equity_usd)

        # Check limits
        if self.state.realized_pnl_today_usd < -self._daily_loss_limit_usd():
            self.state.trading_halted = True
            self.state.halt_reason = "Daily loss cap breached"

        dd = self.state.equity_start_usd - self.state.equity_usd
        if dd > self._max_drawdown_limit_usd():
            self.state.trading_halted = True
            self.state.halt_reason = "Max drawdown breached"

        logger.info(
            "risk_record_fill",
            extra={
                "pnl": realized_pnl_usd,
                "equity_usd": self.state.equity_usd,
                "realized_pnl_today_usd": self.state.realized_pnl_today_usd,
                "trading_halted": self.state.trading_halted,
                "halt_reason": self.state.halt_reason,
            },
        )

    def reset_daily(self) -> None:
        self.state.day_start_ts = time.time()
        self.state.day_start_equity_usd = self.state.equity_usd
        self.state.realized_pnl_today_usd = 0.0
        self.state.trading_halted = False
        self.state.halt_reason = ""
        logger.info("risk_daily_reset")

    def set_kill_switch(self, active: bool, reason: str = "manual") -> None:
        self.state.kill_switch = active
        if active:
            self.state.trading_halted = True
            self.state.halt_reason = f"KillSwitch: {reason}"
        logger.warning("risk_kill_switch_set", extra={"active": active, "reason": reason})

    def summary(self) -> Dict[str, Any]:
        return {
            "params": asdict(self.params),
            "state": asdict(self.state),
            "limits": {
                "risk_per_trade_usd": self.allowed_risk_per_trade_usd(),
                "daily_loss_limit_usd": self._daily_loss_limit_usd(),
                "max_drawdown_limit_usd": self._max_drawdown_limit_usd(),
            },
        }
