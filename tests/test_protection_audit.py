import unittest
from signalbot.protection_audit import classify


class ProtectionAuditTests(unittest.TestCase):
    def setUp(self):
        self.legs = [{'side': 'long', 'quantity': 8}, {'side': 'long', 'quantity': 2}]

    def test_partial_fill_flags_uncertain_protection(self):
        result = classify(self.legs, [{'status': 'PARTIALLY_FILLED', 'executedQty': 4}, {'status': 'NEW', 'executedQty': 0}],
                          [{'symbol': 'BTC-USDT', 'positionSide': 'LONG', 'positionAmt': '4'}], [], 'BTC-USDT')
        self.assertEqual(result['severity'], 'ALERT')
        self.assertFalse(result['protection_confirmed'])
        self.assertTrue(any('Pending' in issue for issue in result['issues']))

    def test_filled_two_legs_never_assumes_protection(self):
        result = classify(self.legs, [{'status': 'FILLED', 'executedQty': 8}, {'status': 'FILLED', 'executedQty': 2}],
                          [{'symbol': 'BTC-USDT', 'positionSide': 'LONG', 'positionAmt': '10'}],
                          [{'symbol': 'BTC-USDT', 'positionSide': 'LONG', 'type': 'STOP_MARKET'},
                           {'symbol': 'BTC-USDT', 'positionSide': 'LONG', 'type': 'TAKE_PROFIT_MARKET'}], 'BTC-USDT')
        self.assertFalse(result['protection_confirmed'])
        self.assertEqual(result['visible_stop_count'], 1)
        self.assertEqual(result['visible_take_profit_count'], 1)

    def test_no_positions_not_false_positive_protection(self):
        result = classify(self.legs, [{'status': 'NEW'}, {'status': 'NEW'}], [], [], 'BTC-USDT')
        self.assertEqual(result['severity'], 'ALERT')
        self.assertFalse(result['protection_confirmed'])

    def test_malformed_plan_fails_closed(self):
        result = classify([{'side': 'long', 'quantity': 8}], [], [], [], 'BTC-USDT')
        self.assertEqual(result['severity'], 'ALERT')
