import unittest
from signalbot.execution_audit import analyze_entry

class EntryAuditTests(unittest.TestCase):
    def test_full_fill_does_not_assert_protection(self):
        result = analyze_entry({'status': 'FILLED', 'origQty': '2', 'executedQty': '2'},
            [{'symbol': 'BTC-USDT', 'positionSide': 'LONG', 'positionAmt': '2'}],
            [{'symbol': 'BTC-USDT', 'positionSide': 'LONG', 'type': 'STOP_MARKET'},
             {'symbol': 'BTC-USDT', 'positionSide': 'LONG', 'type': 'TAKE_PROFIT_MARKET'}],
            'BTC-USDT', 'long')
        self.assertTrue(result['fully_filled'])
        self.assertTrue(result['position_match'])
        self.assertFalse(result['protection_confirmed'])
        self.assertEqual(result['stop_orders_visible'], 1)
        self.assertEqual(result['take_profit_orders_visible'], 1)

    def test_partial_fill(self):
        result = analyze_entry({'status': 'PARTIALLY_FILLED', 'origQty': '4', 'executedQty': '1'},
            [{'symbol': 'BTC-USDT', 'positionSide': 'SHORT', 'positionAmt': '-1'}], [], 'BTC-USDT', 'short')
        self.assertTrue(result['partially_filled'])
        self.assertFalse(result['fully_filled'])

    def test_new_is_not_open_position(self):
        result = analyze_entry({'status': 'NEW', 'origQty': '4', 'executedQty': '0'}, [], [], 'BTC-USDT', 'long')
        self.assertFalse(result['fully_filled'])
        self.assertFalse(result['position_match'])

    def test_cannot_match_other_side(self):
        result = analyze_entry({'status': 'FILLED', 'origQty': '4', 'executedQty': '4'},
            [{'symbol': 'BTC-USDT', 'positionSide': 'SHORT', 'positionAmt': '4'}], [], 'BTC-USDT', 'long')
        self.assertFalse(result['position_match'])
