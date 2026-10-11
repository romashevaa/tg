import unittest
from types import SimpleNamespace
from tv_live_engine import signal_from_evidence
from tv_trade_diagnostics import bingx_pair

class BlockerFixTests(unittest.TestCase):
    def test_nonbtc_symbol_not_rewritten_as_btc(self):
        ev=SimpleNamespace(symbol='SOLUSDT.P', direction='LONG', entry=120, stop_loss=100,
                           targets=[140], leverage=None, margin_type=None)
        signal=signal_from_evidence(ev)
        self.assertEqual(signal.base_asset, 'SOL')
        self.assertEqual(signal.quote_asset, 'USDT')

    def test_btcusd_conversion(self):
        ev=SimpleNamespace(symbol='BTCUSD', direction='SHORT', entry=80000, stop_loss=82000,
                           targets=[78000], leverage=None, margin_type=None)
        self.assertEqual(signal_from_evidence(ev).base_asset,'BTC')

    def test_rejects_unsupported(self):
        ev=SimpleNamespace(symbol='GBPJPY', direction='LONG', entry=1, stop_loss=.9,
                           targets=[1.1], leverage=None, margin_type=None)
        with self.assertRaises(ValueError):
            signal_from_evidence(ev)
