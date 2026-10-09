import sqlite3
import unittest

import tv_watch_policy as policy
import tv_auto_alerts as alerts
from signalbot.risk import pick_take_profit, build_plan, PlanError
from signalbot.config import RiskCfg
from signalbot.models import MarketInfo, ParsedSignal
from signalbot.textparse import find_margin_type


class MarginAndPlanTests(unittest.TestCase):
    def test_margin(self):
        self.assertEqual(find_margin_type('Margin: Isolated x10'), 'ISOLATED')
        self.assertEqual(find_margin_type('ETH SHORT cross margin'), 'CROSSED')
        self.assertIsNone(find_margin_type('BTC LONG x5'))
        self.assertIsNone(find_margin_type('Cross or Isolated?'))

    def test_middle_tp(self):
        self.assertEqual(pick_take_profit([100, 110, 120], 'middle'), 110)
        self.assertEqual(pick_take_profit([100, 110], 'middle'), 100)

    def test_parsed_signal_backwards_compatible(self):
        d = dict(kind='new_signal', confidence=1, base_asset='BTC', quote_asset='USDT',
                 market='futures', side='long', entry_type='market', entry_low=None,
                 entry_high=None, stop_loss=90, take_profits=[110, 120, 130], leverage=5,
                 update_action='none', update_stop_loss=None, refers_to_message_id=None,
                 numbers_from_image=False, stale_hints=[], reason='test')
        sig = ParsedSignal(**d)
        self.assertIsNone(sig.margin_type)


class ClosedIdeaTests(unittest.TestCase):
    def test_terminal_status_and_text(self):
        self.assertIsNotNone(policy.terminal_reason('Trade closed: target reached', ''))
        self.assertIsNotNone(policy.terminal_reason('Trade active', 'Stop loss was reached'))
        self.assertIsNotNone(policy.terminal_reason('Trade active', 'Reached all targets'))
        self.assertIsNone(policy.terminal_reason('Trade active', 'First target reached'))
        self.assertIsNone(policy.terminal_reason('', 'Target 1: 1.10 Stop Loss: 0.99'))

    def test_persistent_closed_records(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE tv_events (idea TEXT, event_key TEXT, time_utc TEXT, status TEXT, body TEXT, images_json TEXT, discovered_utc TEXT)')
        db.execute('INSERT INTO tv_events VALUES (?,?,?,?,?,?,?)',
                   ('AAAABBBB', 'key', '2026-10-09', 'Trade closed: target reached', 'done', '[]', '2026-10-09'))
        policy.initialize(db)
        self.assertEqual(policy.initialize_existing(db), 1)
        self.assertEqual(policy.initialize_existing(db), 0)
        self.assertEqual(policy.closed_ids(db), {'AAAABBBB'})
        db.close()

    def test_discovery_no_duplicate(self):
        db=sqlite3.connect(':memory:')
        alerts.initialize(db)
        def idea(s):
            return {'image_url':s, 'name':'Test '+s,
                    'chart_url':'https://www.tradingview.com/chart/BTCUSDT/'+s+'-title/',
                    'user':{'username':'Altsignals'}}
        a,b=idea('TEST0001'),idea('TEST0002')
        self.assertEqual(alerts.discover(db, set(), fetch=lambda:[a]),1)
        self.assertEqual(alerts.discover(db, set(), fetch=lambda:[a,b]),1)
        self.assertEqual(alerts.discover(db, set(), fetch=lambda:[a,b]),0)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM tv_alert_outbox').fetchone()[0],1)
        db.close()

if __name__ == '__main__':
    unittest.main()
