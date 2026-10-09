from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from decimal import Decimal
from urllib.parse import quote

import httpx

from .models import MarketInfo, OrderPlan

LIVE = ["https://open-api.bingx.com", "https://open-api.bingx.pro"]
VST = ["https://open-api-vst.bingx.com", "https://open-api-vst.bingx.pro"]
FORBIDDEN = re.compile(r"[&=?#\r\n]")
SYMBOL_RE = re.compile(r"^[A-Z0-9]+-[A-Z]+$")
CACHE_TTL = 3600


class BingXError(Exception):
    def __init__(self, code, msg: str):
        super().__init__(f"BingX {code}: {msg}")
        self.code = code


def fmt(value) -> str:
    """Render a parameter without float artefacts or exponent notation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(Decimal(repr(value)), "f")
    return str(value)


def canonical(params: dict) -> str:
    return "&".join(f"{k}={params[k]}" for k in sorted(params))


def sign(secret: str, params: dict) -> str:
    return hmac.new(secret.encode(), canonical(params).encode(), hashlib.sha256).hexdigest()


def build_query(params: dict, signature: str | None) -> str:
    """Signature is computed over raw values; JSON values are URL-encoded only in the request."""
    pairs = []
    for k in sorted(params):
        v = params[k]
        pairs.append(f"{k}={quote(v, safe='') if ('{' in v or '[' in v) else v}")
    if signature:
        pairs.append(f"signature={signature}")
    return "&".join(pairs)


def _decimals(step) -> int:
    d = Decimal(str(step)).normalize()
    return max(0, -d.as_tuple().exponent)


class BingX:
    def __init__(self, api_key: str, secret_key: str, demo: bool = False, timeout: float = 10.0):
        self.api_key = api_key
        self.secret = secret_key
        self.demo = demo
        self.http = httpx.AsyncClient(timeout=timeout)
        self._contracts: tuple[float, dict] | None = None
        self._spot_symbols: tuple[float, dict] | None = None
        self._kline_lock = asyncio.Lock()
        self._kline_next = 0.0

    async def close(self) -> None:
        await self.http.aclose()

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key and self.secret)

    async def _request(self, method: str, path: str, params: dict | None = None, signed: bool = True,
                       futures: bool = True):
        raw = {k: (v if isinstance(v, str) else fmt(v)) for k, v in (params or {}).items() if v is not None}
        for k, v in raw.items():
            # JSON values (attached stop loss / take profit) legitimately contain other symbols.
            if not v.startswith("{") and FORBIDDEN.search(v):
                raise ValueError(f"Forbidden character in parameter {k}")
        raw["timestamp"] = str(int(time.time() * 1000))
        signature = None
        headers = {}
        if signed:
            if not self.has_keys:
                raise BingXError("no_keys", "API keys are not configured")
            raw["recvWindow"] = "5000"
            signature = sign(self.secret, raw)
            headers["X-BX-APIKEY"] = self.api_key
        query = build_query(raw, signature)

        # The VST host only simulates futures; spot always talks to the live host.
        bases = VST if (self.demo and futures) else LIVE
        last_error: Exception | None = None
        for base in bases:
            try:
                resp = await self.http.request(method, f"{base}{path}?{query}", headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_error = e
                continue
            body = resp.json()
            if body.get("code") not in (0, "0"):
                raise BingXError(body.get("code"), body.get("msg", ""))
            return body.get("data")
        raise last_error  # type: ignore[misc]

    # market data

    async def _futures_contracts(self) -> dict:
        if self._contracts and time.time() - self._contracts[0] < CACHE_TTL:
            return self._contracts[1]
        data = await self._request("GET", "/openApi/swap/v2/quote/contracts", signed=False)
        table = {c["symbol"]: c for c in data or []}
        self._contracts = (time.time(), table)
        return table

    async def _spot_table(self) -> dict:
        if self._spot_symbols and time.time() - self._spot_symbols[0] < CACHE_TTL:
            return self._spot_symbols[1]
        data = await self._request("GET", "/openApi/spot/v1/common/symbols", signed=False, futures=False)
        table = {s["symbol"]: s for s in (data or {}).get("symbols", [])}
        self._spot_symbols = (time.time(), table)
        return table

    async def price(self, symbol: str, market: str) -> float:
        if market == "futures":
            data = await self._request("GET", "/openApi/swap/v1/ticker/price", {"symbol": symbol}, signed=False)
            item = data[0] if isinstance(data, list) else data
            return float(item["price"])
        data = await self._request(
            "GET", "/openApi/spot/v2/ticker/price", {"symbol": symbol}, signed=False, futures=False
        )
        item = data[0] if isinstance(data, list) else data
        trades = item.get("trades")
        return float(trades[0]["price"] if trades else item["price"])

    async def market_info(self, symbol: str, market: str) -> MarketInfo | None:
        """Symbol rules plus the live price, or None when the pair cannot be traded."""
        if not SYMBOL_RE.match(symbol):
            return None
        if market == "futures":
            c = (await self._futures_contracts()).get(symbol)
            if not c or int(c.get("status", 0)) != 1 or str(c.get("apiStateOpen", "true")).lower() != "true":
                return None
            return MarketInfo(
                symbol=symbol,
                market="futures",
                price=await self.price(symbol, "futures"),
                price_precision=int(c["pricePrecision"]),
                qty_precision=int(c["quantityPrecision"]),
                min_qty=float(c.get("tradeMinQuantity") or 0),
                min_notional=float(c.get("tradeMinUSDT") or 0),
                max_leverage=int(min(c.get("maxLongLeverage") or 1, c.get("maxShortLeverage") or 1)),
            )
        s = (await self._spot_table()).get(symbol)
        if not s or int(s.get("status", 0)) != 1:
            return None
        return MarketInfo(
            symbol=symbol,
            market="spot",
            price=await self.price(symbol, "spot"),
            price_precision=_decimals(s["tickSize"]),
            qty_precision=_decimals(s["stepSize"]),
            min_qty=float(s.get("minQty") or 0),
            min_notional=float(s.get("minNotional") or 0),
        )

    async def tradable_market(self, symbol: str, prefer: str = "futures") -> str | None:
        """Which market lists the pair, preferring `prefer`. Uses cached symbol tables only."""
        if not SYMBOL_RE.match(symbol):
            return None
        tables = {"futures": await self._futures_contracts(), "spot": await self._spot_table()}
        for market in (prefer, "spot" if prefer == "futures" else "futures"):
            if symbol in tables[market]:
                return market
        return None

    async def klines(self, symbol: str, market: str, interval: str, start_ms: int, end_ms: int,
                     limit: int = 1440) -> list[tuple[int, float, float, float, float]]:
        """Candles as (open time ms, open, high, low, close), oldest first."""
        # Public market data is limited to about one request per second per IP.
        async with self._kline_lock:
            wait = self._kline_next - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._kline_next = time.monotonic() + 1.1
        params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": limit}
        if market == "futures":
            data = await self._request("GET", "/openApi/swap/v3/quote/klines", params, signed=False)
        else:
            params["limit"] = min(limit, 1000)
            data = await self._request("GET", "/openApi/spot/v2/market/kline", params, signed=False, futures=False)
        out = []
        for row in data or []:
            if isinstance(row, dict):
                out.append((int(row["time"]), float(row["open"]), float(row["high"]), float(row["low"]),
                            float(row["close"])))
            else:
                out.append((int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        out.sort()
        return [c for c in out if start_ms - 1 <= c[0] <= end_ms]

    # account

    async def equity(self, market: str) -> float:
        if market == "futures":
            data = await self._request("GET", "/openApi/swap/v3/user/balance")
            rows = data if isinstance(data, list) else [data.get("balance", data)]
            for row in rows:
                if row.get("asset") == "USDT":
                    return float(row.get("equity") or row.get("balance") or 0)
            return 0.0
        return await self.spot_free("USDT")

    async def spot_free(self, asset: str) -> float:
        data = await self._request("GET", "/openApi/spot/v1/account/balance", futures=False)
        for row in (data or {}).get("balances", []):
            if row.get("asset") == asset:
                return float(row.get("free") or 0)
        return 0.0

    async def futures_positions(self, symbol: str | None = None) -> list[dict]:
        """Read actual exchange positions; exclude zero-quantity records."""
        data = await self._request('GET', '/openApi/swap/v2/user/positions',
                                   {'symbol': symbol} if symbol else None)
        if isinstance(data, dict):
            data = data.get('positions', data.get('position', []))
        if isinstance(data, dict):
            data = [data]
        return [p for p in (data or []) if float(p.get('positionAmt') or 0) != 0]

    async def futures_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Read outstanding exchange orders, including protective orders if returned."""
        data = await self._request('GET', '/openApi/swap/v2/trade/openOrders',
                                   {'symbol': symbol} if symbol else None)
        if isinstance(data, dict):
            data = data.get('orders', data.get('order', []))
        if isinstance(data, dict):
            data = [data]
        return list(data or [])

    async def futures_order(self, symbol: str, order_id: str | int | None = None,
                            client_order_id: str | None = None) -> dict:
        """Query an entry order's actual exchange status (NEW, FILLED, etc.)."""
        if (order_id is None) == (client_order_id is None):
            raise ValueError('Supply exactly one of order_id or client_order_id')
        params = {'symbol': symbol}
        if order_id is not None:
            params['orderId'] = order_id
        else:
            params['clientOrderId'] = client_order_id
        data = await self._request('GET', '/openApi/swap/v2/trade/order', params)
        if isinstance(data, dict) and isinstance(data.get('order'), dict):
            return data['order']
        if not isinstance(data, dict):
            raise ValueError('Unexpected BingX order response')
        return data

    async def open_positions(self) -> int:
        data = await self._request("GET", "/openApi/swap/v2/user/positions")
        return sum(1 for p in data or [] if float(p.get("positionAmt") or 0) != 0)

    async def hedge_mode(self) -> bool:
        data = await self._request("GET", "/openApi/swap/v1/positionSide/dual")
        return str(data.get("dualSidePosition")).lower() == "true"

    # futures trading

    async def open_futures(self, plan: OrderPlan, margin_type: str) -> dict:
        long = plan.side == "long"
        hedge = await self.hedge_mode()
        position_side = ("LONG" if long else "SHORT") if hedge else "BOTH"

        if margin_type not in ("ISOLATED", "CROSSED"):
            raise ValueError("Invalid margin mode")
        # Fail closed on a rejected switch; never silently trade using an unverified mode.
        await self._request(
            "POST", "/openApi/swap/v2/trade/marginType", {"symbol": plan.symbol, "marginType": margin_type}
        )
        await self._request(
            "POST",
            "/openApi/swap/v2/trade/leverage",
            {"symbol": plan.symbol, "side": position_side, "leverage": plan.leverage},
        )

        params: dict = {
            "symbol": plan.symbol,
            "side": "BUY" if long else "SELL",
            "positionSide": position_side,
            "type": plan.order_type,
            "quantity": plan.quantity,
            "clientOrderId": plan.client_id,
        }
        if plan.order_type == "LIMIT":
            params["price"] = plan.limit_price
            params["timeInForce"] = "GTC"
        if plan.stop_loss is not None:
            params["stopLoss"] = json.dumps(
                {"type": "STOP_MARKET", "stopPrice": plan.stop_loss, "workingType": "MARK_PRICE"},
                separators=(",", ":"),
            )
        if plan.take_profit is not None:
            params["takeProfit"] = json.dumps(
                {"type": "TAKE_PROFIT_MARKET", "stopPrice": plan.take_profit, "workingType": "MARK_PRICE"},
                separators=(",", ":"),
            )
        data = await self._request("POST", "/openApi/swap/v2/trade/order", params)
        return data.get("order", data) if isinstance(data, dict) else {"raw": data}

    async def prepare_hedge_close(self, symbol: str, side: str, expected_quantity: float) -> dict:
        """Read-only preflight: close only an exactly matched Hedge leg.

        No writes. If the live quantity changed or multiple legs are present,
        require a fresh manual decision instead of guessing.
        """
        if not SYMBOL_RE.fullmatch(symbol) or side not in ("long", "short"):
            raise ValueError("Invalid symbol or side")
        if not await self.hedge_mode():
            raise RuntimeError("Expected BingX Hedge Mode; refusing to close")
        matching_side = side.upper()
        positions = await self.futures_positions(symbol)
        matches = [p for p in positions if p.get("symbol") == symbol
                   and str(p.get("positionSide", "")).upper() == matching_side
                   and abs(float(p.get("positionAmt") or 0)) > 0]
        if len(matches) != 1:
            raise RuntimeError("Cannot identify exactly one open Hedge leg for closing")
        actual = abs(float(matches[0]["positionAmt"]))
        if expected_quantity <= 0 or abs(actual - expected_quantity) > max(1e-9, actual * 1e-6):
            raise RuntimeError("Actual position size differs from recorded plan; manual reconciliation required")
        return {"symbol": symbol, "side": "SELL" if side == "long" else "BUY",
                "positionSide": matching_side, "type": "MARKET", "quantity": actual}

    async def close_hedge_exact(self, symbol: str, side: str, expected_quantity: float) -> dict:
        """Close one verified Hedge leg; do not cancel unrelated symbol-wide orders."""
        params = await self.prepare_hedge_close(symbol, side, expected_quantity)
        return await self._request("POST", "/openApi/swap/v2/trade/order", params)

    async def futures_margin_mode(self, symbol: str):
        """Read current exchange margin mode, without modification."""
        return await self._request("GET", "/openApi/swap/v2/trade/marginType", {"symbol": symbol})

    async def close_futures(self, symbol: str) -> dict:
        """Cancel resting orders for the symbol and close its position at market."""
        cancelled = await self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        closed = await self._request("POST", "/openApi/swap/v2/trade/closeAllPositions", {"symbol": symbol})
        return {"cancelled": cancelled, "closed": closed}

    async def cancel_futures_orders(self, symbol: str) -> dict:
        data = await self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        return {"cancelled": data}

    # spot trading

    async def open_spot(self, plan: OrderPlan, info: MarketInfo, slippage_pct: float) -> dict:
        params: dict = {
            "symbol": plan.symbol,
            "side": "BUY",
            "type": plan.order_type,
            "quantity": plan.quantity,
            "newClientOrderId": plan.client_id,
        }
        if plan.order_type == "LIMIT":
            params["price"] = plan.limit_price
            params["timeInForce"] = "GTC"
        order = await self._request("POST", "/openApi/spot/v1/trade/order", params, futures=False)
        result = {"entry": order, "exit": None}

        filled = float((order or {}).get("executedQty") or 0)
        if filled <= 0:
            result["exit"] = "entry not filled yet: stop loss and take profit are not placed"
            return result

        # The fee is charged in the bought coin, so sell what is actually on the balance.
        base = plan.symbol.split("-")[0]
        factor = 10**info.qty_precision
        qty = int(min(filled, await self.spot_free(base)) * factor + 1e-9) / factor
        if qty <= 0:
            return result
        sl, tp = plan.stop_loss, plan.take_profit
        if sl is not None and tp is not None:
            result["exit"] = await self._request(
                "POST",
                "/openApi/spot/v1/oco/order",
                {
                    "symbol": plan.symbol,
                    "side": "SELL",
                    "quantity": qty,
                    "limitPrice": tp,
                    "triggerPrice": sl,
                    "orderPrice": round(sl * (1 - slippage_pct / 100), info.price_precision),
                },
                futures=False,
            )
        elif sl is not None:
            result["exit"] = await self._request(
                "POST",
                "/openApi/spot/v1/trade/order",
                {"symbol": plan.symbol, "side": "SELL", "type": "TAKE_STOP_MARKET", "quantity": qty, "stopPrice": sl},
                futures=False,
            )
        elif tp is not None:
            result["exit"] = await self._request(
                "POST",
                "/openApi/spot/v1/trade/order",
                {"symbol": plan.symbol, "side": "SELL", "type": "LIMIT", "quantity": qty, "price": tp,
                 "timeInForce": "GTC"},
                futures=False,
            )
        return result

    async def close_spot(self, symbol: str, quantity: float, info: MarketInfo) -> dict:
        """Cancel resting orders and sell at most the quantity this bot bought."""
        cancelled = await self._request(
            "POST", "/openApi/spot/v1/trade/cancelOpenOrders", {"symbol": symbol}, futures=False
        )
        factor = 10**info.qty_precision
        qty = int(min(quantity, await self.spot_free(symbol.split("-")[0])) * factor + 1e-9) / factor
        sold = None
        if qty > 0:
            sold = await self._request(
                "POST",
                "/openApi/spot/v1/trade/order",
                {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty},
                futures=False,
            )
        return {"cancelled": cancelled, "sold": sold}
