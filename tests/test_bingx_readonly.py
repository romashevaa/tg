import unittest
from unittest.mock import AsyncMock
from signalbot.bingx import BingX


class BingXReadonlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = BingX('fake', 'fake')
        self.api._request = AsyncMock()

    async def asyncTearDown(self):
        await self.api.close()

    async def test_positions_filter_zero(self):
        self.api._request.return_value = [
            {'symbol': 'BTC-USDT', 'positionAmt': '0'},
            {'symbol': 'ETH-USDT', 'positionAmt': '-0.2'}]
        rows = await self.api.futures_positions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['symbol'], 'ETH-USDT')
        self.api._request.assert_awaited_once_with(
            'GET', '/openApi/swap/v2/user/positions', None)

    async def test_open_orders(self):
        self.api._request.return_value = {'orders': [{'orderId': 99, 'status': 'NEW'}]}
        rows = await self.api.futures_open_orders('BTC-USDT')
        self.assertEqual(rows[0]['orderId'], 99)
        self.api._request.assert_awaited_once_with(
            'GET', '/openApi/swap/v2/trade/openOrders', {'symbol': 'BTC-USDT'})

    async def test_order_lookup(self):
        self.api._request.return_value = {'order': {'status': 'FILLED', 'executedQty': '1'}}
        row = await self.api.futures_order('BTC-USDT', client_order_id='abc')
        self.assertEqual(row['status'], 'FILLED')
        self.api._request.assert_awaited_once_with(
            'GET', '/openApi/swap/v2/trade/order',
            {'symbol': 'BTC-USDT', 'clientOrderId': 'abc'})

    async def test_order_lookup_requires_unique_reference(self):
        with self.assertRaises(ValueError):
            await self.api.futures_order('BTC-USDT')
        with self.assertRaises(ValueError):
            await self.api.futures_order('BTC-USDT', order_id=10, client_order_id='abc')

if __name__ == '__main__':
    unittest.main()
