from __future__ import annotations

from dataclasses import asdict

from .bingx import BingX
from .config import Config
from .models import MarketInfo, OrderPlan


class Executor:
    """Sends orders according to TRADING_MODE. Returns (status, exchange response)."""

    def __init__(self, exchange: BingX, cfg: Config):
        self.exchange = exchange
        self.mode = cfg.trading_mode

    def is_real(self, market: str) -> bool:
        if self.mode == "live":
            return True
        # BingX simulated trading (VST) exists for futures only.
        return self.mode == "demo" and market == "futures"

    async def open(self, plan: OrderPlan, info: MarketInfo, cfg: Config) -> tuple[str, dict]:
        if not self.is_real(plan.market):
            return "shadow", {"shadow": True, "plan": asdict(plan)}
        if plan.market == "futures":
            return "executed", await self.exchange.open_futures(plan, cfg.risk.margin_type)
        return "executed", await self.exchange.open_spot(plan, info, cfg.risk.spot_slippage_pct)

    async def close(self, plan: OrderPlan, info: MarketInfo | None) -> tuple[str, dict]:
        if not self.is_real(plan.market):
            return "closed", {"shadow": True}
        if plan.market == "futures":
            return "closed", await self.exchange.close_futures(plan.symbol)
        if info is None:
            raise RuntimeError(f"{plan.symbol} is not tradable on spot right now")
        return "closed", await self.exchange.close_spot(plan.symbol, plan.quantity, info)

    async def cancel(self, plan: OrderPlan) -> tuple[str, dict]:
        """Cancel a resting entry order. Positions that are already open stay untouched."""
        if not self.is_real(plan.market):
            return "cancelled", {"shadow": True}
        if plan.market == "futures":
            return "cancelled", await self.exchange.cancel_futures_orders(plan.symbol)
        data = await self.exchange._request(
            "POST", "/openApi/spot/v1/trade/cancelOpenOrders", {"symbol": plan.symbol}, futures=False
        )
        return "cancelled", {"cancelled": data}
