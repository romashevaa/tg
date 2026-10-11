import asyncio
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from signalbot.models import MarketInfo
from signalbot.config import load_config
from tv_live_engine import execute, schema

class FlowTests(unittest.TestCase):
    def test_legacy_two_order_execution_is_disabled(self):
        ev=SimpleNamespace(category='NEW_CALL', confidence='HIGH', symbol='WLDUSDT.P', direction='LONG',
           entry=.5538, stop_loss=.4778, targets=[.6299], leverage=None, margin_type=None)
        cfg=load_config('config.toml')
        info=MarketInfo('WLD-USDT','futures',.5538,5,1,1,1,25)
        x=SimpleNamespace(
            market_info=AsyncMock(return_value=info), hedge_mode=AsyncMock(return_value=True),
            futures_positions=AsyncMock(return_value=[]), futures_open_orders=AsyncMock(return_value=[]),
            equity=AsyncMock(return_value=100),open_positions=AsyncMock(return_value=0),
            open_futures=AsyncMock(return_value={'orderId':'123'}),
            futures_order=AsyncMock(return_value={'status':'NEW'}), close=AsyncMock())
        db=sqlite3.connect(':memory:')
        with patch.dict(os.environ,{'TRADING_MODE':'live','TV_LIVE_EXECUTION':'YES','TV_LIVE_ACK':'I_ACCEPT_TWO_REAL_ORDERS','TV_SMART_ENTRY_LIVE_GATE':'YES','BINGX_API_KEY':'test','BINGX_SECRET_KEY':'test'}), \
             patch('tv_live_engine.BingX',return_value=x), \
             patch('tv_smart_entry.inspect', new=AsyncMock(return_value=SimpleNamespace(status='READY_REVIEW', reason='confirmed'))):
            cfg=load_config('config.toml')
            result=asyncio.run(execute(db,'WLDTEST','author',ev,datetime.now(timezone.utc),cfg))
            self.assertEqual(result[0],'CHECK')
            self.assertIn('ONE_ENTRY_NOT_ENABLED', result[1])
            x.open_futures.assert_not_awaited()
            second=asyncio.run(execute(db,'WLDTEST','author',ev,datetime.now(timezone.utc),cfg))
            self.assertEqual(second[0],'CHECK')
            x.open_futures.assert_not_awaited()
