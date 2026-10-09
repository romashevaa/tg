import unittest
from unittest.mock import AsyncMock
from signalbot.bingx import BingX

class HedgeCloseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = BingX('fake','fake')
        self.api.hedge_mode = AsyncMock(return_value=True)
        self.api.futures_positions = AsyncMock(return_value=[
            {'symbol':'BTC-USDT','positionSide':'LONG','positionAmt':'0.02'},
            {'symbol':'BTC-USDT','positionSide':'SHORT','positionAmt':'-0.03'}])
        self.api._request = AsyncMock(return_value={'orderId':42})
    async def asyncTearDown(self):
        await self.api.close()
    async def test_long_close_targets_only_long(self):
        result=await self.api.close_hedge_exact('BTC-USDT','long',.02)
        self.assertEqual(result['orderId'],42)
        self.api._request.assert_awaited_once_with('POST','/openApi/swap/v2/trade/order',
            {'symbol':'BTC-USDT','side':'SELL','positionSide':'LONG','type':'MARKET','quantity':.02})
    async def test_short_close_targets_only_short(self):
        result=await self.api.prepare_hedge_close('BTC-USDT','short',.03)
        self.assertEqual((result['side'],result['positionSide']),('BUY','SHORT'))
        self.api._request.assert_not_awaited()
    async def test_mismatch_fails_before_trade(self):
        with self.assertRaises(RuntimeError):
            await self.api.close_hedge_exact('BTC-USDT','long',.01)
        self.api._request.assert_not_awaited()
    async def test_zero_position_fails(self):
        self.api.futures_positions.return_value=[]
        with self.assertRaises(RuntimeError):
            await self.api.close_hedge_exact('BTC-USDT','long',.02)
        self.api._request.assert_not_awaited()
    async def test_one_way_fails(self):
        self.api.hedge_mode.return_value=False
        with self.assertRaises(RuntimeError):
            await self.api.close_hedge_exact('BTC-USDT','long',.02)
        self.api._request.assert_not_awaited()
