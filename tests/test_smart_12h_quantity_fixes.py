import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tv_smart_entry import aggregate_closed_candles, inspect, INTERVAL_MS

H4 = INTERVAL_MS['4h']
H12 = INTERVAL_MS['12h']


def evidence(category='NEW_CALL'):
    return SimpleNamespace(symbol='BTCUSD', timeframe='12h', category=category,
        confidence='HIGH', direction='LONG', entry=100, stop_loss=95, targets=[110])


class TwelveHourTests(unittest.TestCase):
    def test_reject_incomplete_bucket(self):
        rows = [(i * H4, 100, 101, 99, 100) for i in range(11)]
        result = aggregate_closed_candles(rows, source_interval='4h', target_interval='12h', now_ms=12*H4)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[-1][0], 6*H4)

    def test_reject_missing_middle_bar(self):
        rows = [(i * H4, 100, 101, 99, 100) for i in (0, 2, 3, 4, 5)]
        result = aggregate_closed_candles(rows, source_interval='4h', target_interval='12h', now_ms=6*H4)
        self.assertEqual([r[0] for r in result], [3*H4])

    def test_exact_ohlc_aggregation(self):
        rows = [(0, 100, 102, 99, 101), (H4, 101, 105, 100, 104), (2*H4, 104, 106, 103, 105)]
        result = aggregate_closed_candles(rows, source_interval='4h', target_interval='12h', now_ms=3*H4)
        self.assertEqual(result, [(0,100,106,99,105)])

    def test_inspect_queries_four_hour_bars(self):
        async def run():
            exchange = SimpleNamespace(market_info=AsyncMock(return_value=SimpleNamespace()),
                klines=AsyncMock(return_value=[]))
            result = await inspect(exchange, evidence(), now=datetime.fromtimestamp(48*3600, timezone.utc))
            self.assertEqual(result.status, 'CHECK')
            self.assertEqual(exchange.klines.call_args.args[2], '4h')
        asyncio.run(run())

    def test_conditional_fails_closed(self):
        async def run():
            exchange = SimpleNamespace(market_info=AsyncMock(return_value=SimpleNamespace()),
                klines=AsyncMock(return_value=[]))
            result = await inspect(exchange, evidence('CONDITIONAL'))
            self.assertEqual(result.status, 'CHECK')
            exchange.klines.assert_not_awaited()
        asyncio.run(run())

if __name__ == '__main__': unittest.main()
