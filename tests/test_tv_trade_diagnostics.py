import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from tv_trade_diagnostics import age_label, bingx_pair, evaluate, targets_80_20


def ev(**kw):
    return SimpleNamespace(**({
        'category':'NEW_CALL','confidence':'HIGH','direction':'LONG',
        'symbol':'ETHUSD','entry':2488.92,'stop_loss':2400.0,'targets':[2600.0]
    } | kw))


class DiagnosticsTests(unittest.TestCase):
    def test_age(self):
        now=datetime(2026,10,10,12,0,tzinfo=timezone.utc)
        self.assertEqual(age_label(now-timedelta(minutes=17),now),'17 хв тому')
    def test_pair(self):
        self.assertEqual(bingx_pair('BTCUSD'),'BTC-USDT')
        self.assertEqual(bingx_pair('WLDUSDT.P'),'WLD-USDT')
        self.assertIsNone(bingx_pair('EURCHF'))
    def test_incomplete(self):
        self.assertEqual(evaluate(ev(entry=None))[0],'SKIP')
        self.assertEqual(evaluate(ev(category='ANALYSIS'))[0],'SKIP')
    def test_price(self):
        self.assertEqual(evaluate(ev(),2610,True)[0],'SKIP')
        self.assertEqual(evaluate(ev(),2500,True)[0],'READY_FOR_EXECUTION_CHECK')
    def test_tp_split_candidate(self):
        self.assertEqual(targets_80_20(ev(entry=10,targets=[14])),(12,14))

if __name__=='__main__': unittest.main()
