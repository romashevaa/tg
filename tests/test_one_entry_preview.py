import unittest
from types import SimpleNamespace as Obj
from signalbot.one_entry import preview

class PreviewTests(unittest.TestCase):
    def plan(self, qty=10, side='long'):
        return Obj(symbol='BTC-USDT', side=side, quantity=qty, entry_price=100,
                   stop_loss=90 if side=='long' else 110)
    def market(self, minimum=1, notional=1, prec=2):
        return Obj(qty_precision=prec, min_qty=minimum, min_notional=notional)
    def test_no_increased_quantity(self):
        x=preview(self.plan(10.03), self.market(), 110, 120)
        self.assertEqual(x.entry_quantity,'10.03')
        self.assertAlmostEqual(float(x.tp1_quantity)+float(x.tp2_quantity),10.03)
        self.assertTrue(x.can_stage_exits)
    def test_small_second_leg_fails(self):
        x=preview(self.plan(2),self.market(minimum=1),110,120)
        self.assertTrue(x.can_stage_exits)
        self.assertEqual(x.exit_mode, 'SINGLE_TP_100')
        self.assertEqual(x.tp1_quantity, '2')
        self.assertEqual(x.tp2_quantity, '0')
    def test_min_notional_per_exit(self):
        x=preview(self.plan(10),self.market(notional=300),110,120)
        self.assertTrue(x.can_stage_exits)
        self.assertEqual(x.exit_mode, 'SINGLE_TP_100')
    def test_bad_direction(self):
        x=preview(self.plan(),self.market(),95,120)
        self.assertFalse(x.can_stage_exits)
    def test_short_ordering(self):
        self.assertTrue(preview(self.plan(10,'short'),self.market(),90,80).can_stage_exits)
    def test_no_exchange_dependency(self):
        self.assertEqual(preview(self.plan(),self.market(),110,120).entry_quantity,'10')

if __name__=='__main__': unittest.main()
