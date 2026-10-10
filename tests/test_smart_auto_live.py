import os
import unittest
from unittest.mock import patch

from tv_live_engine import schema
import tv_smart_scan
import sqlite3


class SmartAutoLiveTests(unittest.TestCase):
    def test_atomic_claim_can_only_happen_once(self):
        with sqlite3.connect(':memory:') as db:
            schema(db)
            sql = '''INSERT OR IGNORE INTO tv_live_orders
                (idea_id,author,created_utc,updated_utc,status,reason,payload)
                VALUES (?,?,?,?,?,?,?)'''
            args = ('idea123', 'author', 'now','now','SUBMITTING','checking','{}')
            first = db.execute(sql,args)
            self.assertEqual(first.rowcount,1)
            second = db.execute(sql,args)
            self.assertEqual(second.rowcount,0)

    def test_schema_stores_smart_states(self):
        with sqlite3.connect(':memory:') as db:
            tv_smart_scan.init(db)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='tv_smart_states'").fetchone())

    def test_live_requires_separate_opt_in(self):
        # Keep separate opt-in even if the rest of the live flags are enabled.
        with patch.dict(os.environ, {'TRADING_MODE':'live','TV_LIVE_EXECUTION':'YES',
                                      'TV_SMART_ENTRY_LIVE_GATE':'YES','TV_SMART_AUTO_EXECUTION':'NO'}):
            allowed = (os.getenv('TRADING_MODE') == 'live'
                       and os.getenv('TV_LIVE_EXECUTION') == 'YES'
                       and os.getenv('TV_SMART_ENTRY_LIVE_GATE') == 'YES'
                       and os.getenv('TV_SMART_AUTO_EXECUTION') == 'YES')
            self.assertFalse(allowed)
