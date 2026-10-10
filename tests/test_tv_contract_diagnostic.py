import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tv_live_catchup import contract_diagnostic

class ContractDiagnosticTests(unittest.TestCase):
    def test_crypto_mapping_and_listing(self):
        exchange = SimpleNamespace(market_info=AsyncMock(return_value=SimpleNamespace(price=83200.0)))
        result = asyncio.run(contract_diagnostic(SimpleNamespace(symbol='BTCUSD'), exchange))
        self.assertIn('pair=BTC-USDT', result)
        self.assertIn('contract=LISTED', result)
        self.assertIn('price=83200', result)
        exchange.market_info.assert_awaited_once_with('BTC-USDT', 'futures')

    def test_fx_never_looked_up_as_crypto(self):
        exchange = SimpleNamespace(market_info=AsyncMock())
        result = asyncio.run(contract_diagnostic(SimpleNamespace(symbol='EURCHF'), exchange))
        self.assertIn('contract=UNSUPPORTED_NON_USDT', result)
        exchange.market_info.assert_not_called()

    def test_not_listed_and_network_error(self):
        exchange = SimpleNamespace(market_info=AsyncMock(return_value=None))
        self.assertIn('NOT_LISTED', asyncio.run(contract_diagnostic(SimpleNamespace(symbol='ETHUSD'), exchange)))
        exchange.market_info.side_effect = TimeoutError('timeout')
        self.assertIn('UNVERIFIED (TimeoutError)', asyncio.run(contract_diagnostic(SimpleNamespace(symbol='ETHUSD'), exchange)))
