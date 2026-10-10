import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from tv_timeframe_policy import freshness, normalize_timeframe, untouched_since_publication

class TimeframePolicyTests(unittest.TestCase):
    def test_timeframe(self):
        self.assertEqual(normalize_timeframe('4H'), '4h')
        self.assertEqual(normalize_timeframe('weekly'), '1w')
        self.assertIsNone(normalize_timeframe(None))
    def test_age(self):
        now=datetime.now(timezone.utc)
        published=now-timedelta(minutes=103)
        self.assertTrue(freshness(published,'1h',now)[0])
        self.assertTrue(freshness(published,'4h',now)[0])
        self.assertFalse(freshness(published,None,now)[0])
        self.assertFalse(freshness(now-timedelta(days=10),'1d',now)[0])
    def test_candle_replay_rejects_entry_touch(self):
        now=datetime.now(timezone.utc)
        pub=now-timedelta(minutes=90)
        class Ex:
            async def _request(self,*args,**kw):
                return [[int(pub.timestamp()*1000), 99, 102, 98, 100], [int(now.timestamp()*1000), 101, 102, 101, 102]]
        ev=SimpleNamespace(direction='LONG',entry=100,stop_loss=90,targets=[110])
        good,reason=asyncio.run(untouched_since_publication(Ex(),'TEST-USDT',ev,pub,now))
        self.assertFalse(good)
        self.assertIn('ENTRY',reason)
    def test_missing_history_fails_closed(self):
        now=datetime.now(timezone.utc)
        pub=now-timedelta(hours=3)
        class Ex:
            async def _request(self,*args,**kw): return []
        ev=SimpleNamespace(direction='LONG',entry=100,stop_loss=90,targets=[110])
        self.assertFalse(asyncio.run(untouched_since_publication(Ex(),'X-USDT',ev,pub,now))[0])
