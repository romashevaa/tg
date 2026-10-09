"""Read-only BingX Futures audit. Does not place, cancel, or edit orders."""
import asyncio
import os
import sys

from signalbot.bingx import BingX


async def main():
    key = os.getenv('BINGX_API_KEY')
    secret = os.getenv('BINGX_SECRET_KEY')
    if not key or not secret:
        raise SystemExit('BINGX_API_KEY and BINGX_SECRET_KEY must be configured in Railway.')
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else None
    if symbol and not symbol.endswith('-USDT'):
        raise SystemExit('Optional symbol must look like BTC-USDT')
    api = BingX(key, secret)
    try:
        print('BingX Futures read-only audit')
        print('Equity (USDT):', await api.equity('futures'))
        print('Position mode:', 'Hedge' if await api.hedge_mode() else 'One-way')
        positions = await api.futures_positions(symbol)
        print('Open positions:', len(positions))
        for position in positions:
            print('POSITION', position.get('symbol'), position.get('positionSide'),
                  'qty=', position.get('positionAmt'), 'entry=', position.get('avgPrice') or position.get('entryPrice'),
                  'PnL=', position.get('unrealizedProfit') or position.get('unrealizedPnL'),
                  'margin=', position.get('marginType'))
        orders = await api.futures_open_orders(symbol)
        print('Open orders:', len(orders))
        for order in orders:
            print('ORDER', order.get('symbol'), order.get('positionSide'),
                  order.get('type'), order.get('status'), 'filled=', order.get('executedQty'),
                  'id=', order.get('orderId'))
        print('AUDIT_OK: no trading requests made')
    finally:
        await api.close()


if __name__ == '__main__':
    asyncio.run(main())
