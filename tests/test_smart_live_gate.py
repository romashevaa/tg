import asyncio
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from tv_live_engine import execute


def ev():
    return SimpleNamespace(symbol='BTCUSD', category='NEW_CALL', confidence='HIGH', direction='LONG',
                           entry=100., stop_loss=95., targets=[110.], timeframe='15m')


def cfg():
    return SimpleNamespace(trading_mode='live',
        validator=SimpleNamespace(max_age_minutes=20),
        secrets=SimpleNamespace(bingx_api_key='test', bingx_secret_key='test'))


class SmartLiveGateTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.published = datetime.now(timezone.utc)
    def tearDown(self):
        self.db.close()
    def run_check(self):
        return asyncio.run(execute(self.db, 'fresh-id', 'author', ev(), self.published, cfg()))
    def test_gate_requires_explicit_activation(self):
        with patch.dict(os.environ, {'TV_LIVE_EXECUTION': 'YES', 'TV_LIVE_ACK': 'I_ACCEPT_TWO_REAL_ORDERS'}, clear=True):
            with patch('tv_live_engine.BingX') as exchange:
                status, reason = self.run_check()
                self.assertEqual(status, 'CHECK')
                self.assertIn('TV_SMART_ENTRY_LIVE_GATE', reason)
                exchange.assert_not_called()
    def test_waiting_never_submits_order(self):
        with patch.dict(os.environ, {'TV_LIVE_EXECUTION':'YES', 'TV_LIVE_ACK':'I_ACCEPT_TWO_REAL_ORDERS',
                                     'TV_SMART_ENTRY_LIVE_GATE':'YES'}):
            with patch('tv_live_engine.BingX') as exchange, patch('tv_smart_entry.inspect',
                return_value=SimpleNamespace(status='WAITING', reason='Not near entry')):
                exchange.return_value.close = AsyncMock()
                status, reason = self.run_check()
                self.assertEqual(status, 'WAITING')
                self.assertIn('Not near entry', reason)
                exchange.return_value.open_futures.assert_not_called()
    def test_smart_api_error_fails_closed(self):
        with patch.dict(os.environ, {'TV_LIVE_EXECUTION':'YES', 'TV_LIVE_ACK':'I_ACCEPT_TWO_REAL_ORDERS',
                                     'TV_SMART_ENTRY_LIVE_GATE':'YES'}):
            with patch('tv_live_engine.BingX') as exchange, patch('tv_smart_entry.inspect', side_effect=RuntimeError('network')):
                exchange.return_value.close = AsyncMock()
                status, reason = self.run_check()
                self.assertEqual(status, 'CHECK')
                exchange.return_value.open_futures.assert_not_called()
