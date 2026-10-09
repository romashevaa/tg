from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger("signalbot.prices")

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}
Row = tuple[int, float, float, float, float]


class PriceHistory:
    """Historical candles for channel evaluation.

    Binance keeps full fine-grained history and is the price most channels quote, so it is asked
    first; BingX, which only serves a few weeks of 5-minute candles, is the fallback.
    """

    SOURCES = ("binance_futures", "binance_spot", "bingx")
    BINANCE = {
        "binance_futures": ("https://fapi.binance.com/fapi/v1/klines", 1500),
        "binance_spot": ("https://data-api.binance.vision/api/v3/klines", 1000),
    }

    def __init__(self, bingx, timeout: float = 15.0):
        self.bingx = bingx
        self.http = httpx.AsyncClient(timeout=timeout)
        self._unknown: set[tuple[str, str]] = set()
        self._failures: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def close(self) -> None:
        await self.http.aclose()

    async def candles(self, symbol: str, start_ms: int, end_ms: int, interval: str = "5m") -> tuple[list[Row], str]:
        """Return (candles oldest first, source name). Empty list when no source has the data."""
        for source in self.SOURCES:
            if (source, symbol) in self._unknown or self._failures.get(source, 0) >= 3:
                continue
            try:
                if source == "bingx":
                    rows = await self._bingx(symbol, start_ms, end_ms, interval)
                else:
                    rows = await self._binance(source, symbol, start_ms, end_ms, interval)
                self._failures[source] = 0
            except LookupError:
                self._unknown.add((source, symbol))
                continue
            except Exception as e:
                # A source that keeps failing (blocked region, outage) is skipped for the rest of the run.
                self._failures[source] = self._failures.get(source, 0) + 1
                log.warning("%s failed for %s: %s", source, symbol, e)
                continue
            # The history must start at the call, not weeks later when the source began listing it.
            if rows and rows[0][0] - start_ms <= 2 * INTERVAL_MS[interval]:
                return rows, source
        return [], ""

    async def _binance(self, source: str, symbol: str, start_ms: int, end_ms: int, interval: str) -> list[Row]:
        url, limit = self.BINANCE[source]
        step = INTERVAL_MS[interval]
        out: list[Row] = []
        cursor = start_ms
        while cursor <= end_ms:
            async with self._lock:
                wait = self._next - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._next = time.monotonic() + 0.25
            resp = await self.http.get(url, params={
                "symbol": symbol.replace("-", ""), "interval": interval,
                "startTime": cursor, "endTime": end_ms, "limit": limit,
            })
            if resp.status_code == 400:
                raise LookupError(symbol)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            out += [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]
            if len(rows) < limit:
                break
            cursor = int(rows[-1][0]) + step
        return out

    async def _bingx(self, symbol: str, start_ms: int, end_ms: int, interval: str) -> list[Row]:
        market = await self.bingx.tradable_market(symbol)
        if market is None:
            raise LookupError(symbol)
        try:
            return await self.bingx.klines(symbol, market, interval, start_ms, end_ms)
        except Exception as e:
            if "100204" in str(e):
                raise LookupError(symbol) from e
            raise
