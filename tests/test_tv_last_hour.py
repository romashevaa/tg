import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tv_last_hour as recent
import tv_auto_alerts as alerts
import tv_author_settings as settings


def entry(i, delta=0):
    return {'image_url':i, 'name':'New long idea', 'created_at':(datetime.now(timezone.utc) + timedelta(minutes=delta)).isoformat(),
            'chart_url':'https://www.tradingview.com/chart/BTCUSDT/'+i+'-Idea/', 'user':{'username':'coinpediamarkets'}}

class RecentPostsTests(unittest.TestCase):
    def test_date_parsing(self):
        self.assertIsNotNone(recent.published_at(entry('ABCD1234')))
        self.assertIsNone(recent.published_at({'name':'No date'}))
    def test_new_author_first_hour_alert(self):
        with sqlite3.connect(':memory:') as db:
            alerts.initialize(db)
            recent_entries=[entry('ABCD1234',-20),entry('ABCD1235',-120)]
            self.assertEqual(alerts.discover(db,set(),fetch=lambda:recent_entries,author_name='coinpediamarkets'),2)
            keys=[r[0] for r in db.execute('SELECT alert_key FROM tv_alert_outbox')]
            self.assertEqual(keys,['idea:ABCD1234'])
            self.assertEqual(alerts.discover(db,set(),fetch=lambda:recent_entries,author_name='coinpediamarkets'),0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tv_alert_outbox').fetchone()[0],1)
    def test_author_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            with sqlite3.connect(str(p/'signalbot.db')) as db:
                settings.set_enabled(db,'coinpediamarkets',True)
            found=recent.run(tmp,1,False,fetch=lambda author: [entry('ABCD1234',-10)] if author=='coinpediamarkets' else [])
            self.assertEqual(len(found),1)

if __name__=='__main__':unittest.main()
