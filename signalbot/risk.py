from __future__ import annotations

import math

from .config import RiskCfg
from .models import MarketInfo, OrderPlan, ParsedSignal


class PlanError(Exception):
    """The signal cannot be turned into an order. `hard` means no manual override makes sense."""

    def __init__(self, message: str, hard: bool = True):
        super().__init__(message)
        self.hard = hard


def floor_to(value: float, precision: int) -> float:
    factor = 10**precision
    return math.floor(value * factor + 1e-9) / factor


def pick_take_profit(tps: list[float], rule: str) -> float | None:
    if not tps:
        return None
    if rule == "last":
        return tps[-1]
    if rule == "middle":
        return tps[(len(tps) - 1) // 2]
    return tps[0]


def pick_leverage(sig: ParsedSignal, info: MarketInfo, cfg: RiskCfg) -> int:
    if info.market == "spot":
        return 1
    lev = sig.leverage if (cfg.use_signal_leverage and sig.leverage) else cfg.default_leverage
    return max(1, min(int(lev), cfg.max_leverage, info.max_leverage or cfg.max_leverage))


def choose_entry(sig: ParsedSignal, price: float, tol_pct: float, limit_when_ran: bool) -> tuple[str, float]:
    """Return (order_type, entry_price) from the called entry and the live price."""
    low, high = sig.entry_low, sig.entry_high
    if low is None and high is None:
        return "MARKET", price
    low = low if low is not None else high
    high = high if high is not None else low
    low, high = min(low, high), max(low, high)
    tol = tol_pct / 100

    if low * (1 - tol) <= price <= high * (1 + tol):
        return "MARKET", price

    long = sig.side == "long"
    ran_away = price > high if long else price < low
    if not ran_away:
        # Price is on the stop side of the zone: a better fill than the call itself.
        return "MARKET", price
    if not limit_when_ran:
        raise PlanError("ціна вже відійшла від зони входу")
    return "LIMIT", high if long else low


def build_plan(
    sig: ParsedSignal,
    info: MarketInfo,
    equity: float,
    cfg: RiskCfg,
    entry_tolerance_pct: float,
    client_id: str,
) -> OrderPlan:
    order_type, entry = choose_entry(sig, info.price, entry_tolerance_pct, cfg.limit_when_price_ran)
    leverage = pick_leverage(sig, info, cfg)
    sl = sig.stop_loss
    tp = pick_take_profit(sig.take_profits, cfg.take_profit)

    if equity <= 0:
        raise PlanError("немає вільного балансу")

    if sl is not None:
        distance = abs(entry - sl)
        if distance <= 0:
            raise PlanError("стоп збігається з ціною входу")
        risk_usdt = equity * cfg.risk_pct / 100
        qty = risk_usdt / distance
    else:
        qty = cfg.fixed_margin_usdt * leverage / entry

    max_margin = equity * cfg.max_margin_pct / 100
    if qty * entry / leverage > max_margin:
        qty = max_margin * leverage / entry

    qty = floor_to(qty, info.qty_precision)
    notional = qty * entry
    if qty <= 0 or qty < info.min_qty or notional < info.min_notional:
        raise PlanError(
            f"позиція {notional:.2f} USDT менша за мінімум біржі "
            f"({info.min_qty} шт / {info.min_notional} USDT)"
        )

    return OrderPlan(
        symbol=info.symbol,
        market=info.market,
        side=sig.side,
        order_type=order_type,
        quantity=qty,
        entry_price=round(entry, info.price_precision),
        limit_price=round(entry, info.price_precision) if order_type == "LIMIT" else None,
        stop_loss=round(sl, info.price_precision) if sl is not None else None,
        take_profit=round(tp, info.price_precision) if tp is not None else None,
        leverage=leverage,
        margin_usdt=round(notional / leverage, 4),
        risk_usdt=round(qty * abs(entry - sl), 4) if sl is not None else round(notional / leverage, 4),
        client_id=client_id,
    )
