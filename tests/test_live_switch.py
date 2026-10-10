import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import railway_start

class LiveSwitchTests(unittest.TestCase):
    def test_shadow_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'signalbot.session').write_bytes(b'local-test-not-a-real-session')
            with patch.object(railway_start, 'DATA', Path(d)), patch.dict(os.environ, {'TRADING_MODE':'shadow','OWNER_ID':'1'}):
                railway_start.guard()
    def test_live_without_explicit_ack_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'signalbot.session').write_bytes(b'local-test-not-a-real-session')
            with patch.object(railway_start, 'DATA', Path(d)), patch.dict(os.environ, {'TRADING_MODE':'live','OWNER_ID':'1','BINGX_LIVE_ACK':''}):
                with self.assertRaises(SystemExit): railway_start.guard()
    def test_live_without_tv_ack_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'signalbot.session').write_bytes(b'local-test-not-a-real-session')
            with patch.object(railway_start, 'DATA', Path(d)), patch.dict(os.environ, {'TRADING_MODE':'live','OWNER_ID':'1','BINGX_LIVE_ACK':'I_UNDERSTAND_REAL_ORDERS','TV_LIVE_EXECUTION':'YES','TV_LIVE_ACK':''}):
                with self.assertRaises(SystemExit): railway_start.guard()
