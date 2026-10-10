import asyncio
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tv_timeframe_recovery import explicit_timeframe, metadata_timeframe, recover


class RecoveryTests(unittest.TestCase):
    def test_explicit_only(self):
        self.assertEqual(explicit_timeframe('Timeframe: 4H, long setup'), '4h')
        self.assertIsNone(explicit_timeframe('entry 1.15, 4 hours ago'))
        self.assertIsNone(explicit_timeframe('Timeframe: 1H, Timeframe: 4H'))
        self.assertEqual(metadata_timeframe({'data': {'interval': '240'}}), '4h')
        self.assertIsNone(metadata_timeframe({'data': {'date': 240}}))

    def test_cache_updated_once(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE tv_full_chart_reviews (idea_id TEXT PRIMARY KEY, result_json TEXT)')
        db.execute('INSERT INTO tv_full_chart_reviews VALUES (?,?)', ('abc', json.dumps({'timeframe': None, 'entry': 2})))
        ev = SimpleNamespace(timeframe=None)
        cfg = SimpleNamespace(secrets=SimpleNamespace(gemini_api_key=None))
        async def check():
            return await recover(db, 'abc', {'data': {'interval': '60'}}, '', ev, cfg)
        result, source = asyncio.run(check())
        self.assertEqual(result.timeframe, '1h')
        self.assertEqual(source, 'tradingview_metadata')
        self.assertEqual(json.loads(db.execute('SELECT result_json FROM tv_full_chart_reviews').fetchone()[0])['entry'], 2)
        result2, source2 = asyncio.run(check())
        self.assertEqual(source2, 'cached')

    def test_unknown_stays_unknown_and_is_cached(self):
        db = sqlite3.connect(':memory:')
        ev = SimpleNamespace(timeframe=None)
        cfg = SimpleNamespace(secrets=SimpleNamespace(gemini_api_key=None))
        idea = SimpleNamespace(status=200, description='No explicit interval')
        with patch('tv_timeframe_recovery.TradingView') as tv:
            tv.return_value.fetch = AsyncMock(return_value=(idea, None))
            tv.return_value.close = AsyncMock()
            asyncio.run(recover(db, 'no-tf', {}, 'https://example.com', ev, cfg))
            asyncio.run(recover(db, 'no-tf', {}, 'https://example.com', ev, cfg))
            self.assertEqual(tv.return_value.fetch.await_count, 1)
        self.assertIsNone(ev.timeframe)
