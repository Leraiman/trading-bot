from __future__ import annotations
from dataclasses import dataclass

@dataclass
class RiskConfig:
    capital_base_usd: float = 10_000.0
    risk_per_trade_bps: float = 50.0       # 50 bps = 0.50%
    daily_loss_cap_bps: float = 200.0      # 2.00%
    max_drawdown_bps: float = 1000.0       # 10.00%
    max_position_usd: float = 5_000.0      # harte Obergrenze
    allow_leverage: bool = False
    max_leverage: float = 1.0

    def daily_loss_cap_usd(self) -> float:
        return self.capital_base_usd * (self.daily_loss_cap_bps / 10_000.0)

    def max_drawdown_usd(self) -> float:
        return self.capital_base_usd * (self.max_drawdown_bps / 10_000.0)
