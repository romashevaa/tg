"""Read-only order status check: python bingx_execution_audit.py SYMBOL SIDE CLIENT_ORDER_ID.

Example: python bingx_execution_audit.py BTC-USDT long your_client_order_id
No database modifications, no trade requests, no API tokens printed.
"""
import asyncio
import os
import sys
from signalbot.bingx import BingX
from signalbot.execution_audit import analyze_entry

async def main(symbol, side, client_order_id):
    client = BingX(os.environ['BINGX_API_KEY'], os.environ['BINGX_SECRET_KEY'])
    try:
        order = await client.futures_order(symbol, client_order_id=client_order_id)
        positions = await client.futures_positions(symbol)
        open_orders = await client.futures_open_orders(symbol)
        result = analyze_entry(order, positions, open_orders, symbol, side)
        print('READ_ONLY_BINGX_EXECUTION_AUDIT')
        for key, val in result.items():
            print(f'{key}: {val}')
        print('No exchange trading requests or database writes.')
    finally:
        await client.close()

if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[2] not in ('long', 'short'):
        raise SystemExit('Usage: python bingx_execution_audit.py BTC-USDT long CLIENT_ORDER_ID')
    asyncio.run(main(*sys.argv[1:]))
