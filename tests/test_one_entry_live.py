import asyncio
import sqlite3
import unittest
from types import SimpleNamespace
from signalbot.models import OrderPlan
from unittest.mock import AsyncMock
from signalbot.one_entry_live import run, exit_order_params
from tv_live_engine import schema


def plan():
    return OrderPlan(symbol='BTC-USDT',side='long',order_type='MARKET',quantity=.01,
        entry_price=83000.,stop_loss=80000.,take_profit=None,client_id='tvtest',
        leverage=5,margin_usdt=100,risk_usdt=5,market='futures',limit_price=None)


class Tests(unittest.TestCase):
    def test_tp_in_hedge_mode(self):
        p=exit_order_params('BTC-USDT','long','.008',85000)
        self.assertEqual(p['side'],'SELL')
        self.assertEqual(p['positionSide'],'LONG')
        self.assertNotIn('reduceOnly',p)
        with self.assertRaises(ValueError):exit_order_params('BTC-USDT','other',1,2)

    def test_ledger_blocks_replay_and_pending_fill_does_not_place_exits(self):
        db=sqlite3.connect(':memory:');schema(db)
        exchange=AsyncMock()
        exchange.hedge_mode.return_value=True
        exchange.futures_positions.side_effect=[[],[{'symbol':'BTC-USDT','positionSide':'LONG','positionAmt':'0.004'}],[]]
        exchange.futures_open_orders.return_value=[]
        exchange.open_futures.return_value={'orderId':123}
        exchange.futures_order.return_value={'status':'PARTIALLY_FILLED'}
        info=SimpleNamespace(qty_precision=4,min_qty=.001,min_notional=1)
        p=plan()
        async def task():
            return await run(db,'idea1','author',exchange,p,info,85000,87000,'ISOLATED')
        status,_=asyncio.run(task())
        self.assertEqual(status,'ONE_ENTRY_PENDING_FILL')
        exchange.place_futures_conditional.assert_not_awaited()
        status,_=asyncio.run(task())
        self.assertEqual(status,'SKIP')
        self.assertEqual(exchange.open_futures.await_count,1)
        db.close()

    def test_full_fill_stages_two_exit_orders_never_certifies_stop(self):
        db=sqlite3.connect(':memory:');schema(db)
        exchange=AsyncMock()
        exchange.hedge_mode.return_value=True
        exchange.futures_positions.side_effect=[[],[{'symbol':'BTC-USDT','positionSide':'LONG','positionAmt':'0.01'}]]
        exchange.futures_open_orders.side_effect=[[],[{'orderId':1},{'orderId':2}]]
        exchange.open_futures.return_value={'orderId':99}
        exchange.futures_order.return_value={'status':'FILLED'}
        exchange.place_futures_conditional.side_effect=[{'orderId':1},{'orderId':2}]
        info=SimpleNamespace(qty_precision=4,min_qty=.001,min_notional=1)
        status,reason=asyncio.run(run(db,'idea2','author',exchange,plan(),info,85000,87000,'ISOLATED'))
        self.assertEqual(status,'ONE_ENTRY_REVIEW')
        self.assertEqual(exchange.place_futures_conditional.await_count,2)
        self.assertIn('verification',reason)
        self.assertNotIn('protected',reason.lower())
        db.close()

    def test_timeout_never_retries(self):
        db=sqlite3.connect(':memory:');schema(db)
        exchange=AsyncMock()
        exchange.hedge_mode.return_value=True
        exchange.futures_positions.return_value=[]
        exchange.futures_open_orders.return_value=[]
        exchange.open_futures.side_effect=TimeoutError()
        info=SimpleNamespace(qty_precision=4,min_qty=.001,min_notional=1)
        p=plan()
        status,_=asyncio.run(run(db,'idea3','author',exchange,p,info,85000,87000,'ISOLATED'))
        self.assertEqual(status,'MANUAL_RECONCILIATION')
        status,_=asyncio.run(run(db,'idea3','author',exchange,p,info,85000,87000,'ISOLATED'))
        self.assertEqual(status,'SKIP')
        self.assertEqual(exchange.open_futures.await_count,1)
        db.close()
