import unittest
from tv_signal_review import review

class SignalReviewTests(unittest.TestCase):
    def test_title_only_is_not_a_trade(self):
        x = {'name': '$LINK | 1H | BUY SETUP |', 'description': 'Potential breakout soon', 'symbol': {'short_name':'LINKUSDT'}}
        self.assertEqual(review(x)['status'],'NEEDS_REVIEW')
    def test_explicit_levels(self):
        x={'name':'BTC LONG','description':'Coin: BTCUSDT Long\nEntry: 65000\nTarget 1: 67000\nTarget 2: 68000\nSL: 64000'}
        r=review(x)
        self.assertEqual(r['status'],'EXPLICIT_LEVELS')
        self.assertEqual(r['entry'],[65000.0])
    def test_missing_entry(self):
        x={'description':'Coin: SOLUSDT Short\nTarget 1: 120\nSL: 150'}
        self.assertEqual(review(x)['status'],'NEEDS_REVIEW')
