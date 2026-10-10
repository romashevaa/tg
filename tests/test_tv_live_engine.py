import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from tv_live_engine import split_quantity, fresh, schema, record, execute
from unittest.mock import patch
import asyncio

class EngineTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_quantity(10, 2, .01), (8.0, 2.0))
        with self.assertRaises(ValueError): split_quantity(.01, 2, .01)
    def test_age(self):
        self.assertTrue(fresh(datetime.now(timezone.utc)-timedelta(minutes=3)))
        self.assertFalse(fresh(datetime.now(timezone.utc)-timedelta(days=2)))
    def test_durable_record_and_no_replay(self):
        db=sqlite3.connect(':memory:');schema(db)
        record(db,'ABC','author','PENDING_VERIFY','sent',{})
        cfg=SimpleNamespace(validator=SimpleNamespace(max_age_minutes=20))
        st, why=asyncio.run(execute(db,'ABC','author',None,None,cfg))
        self.assertEqual(st,'PENDING_VERIFY')
    def test_old_signal_never_trades(self):
        db=sqlite3.connect(':memory:')
        cfg=SimpleNamespace(validator=SimpleNamespace(max_age_minutes=20))
        st,_=asyncio.run(execute(db,'new','author',None,datetime.now(timezone.utc)-timedelta(days=1),cfg))
        self.assertEqual(st,'SKIP')
