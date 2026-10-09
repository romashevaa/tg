import sqlite3
import unittest
import tv_auto_alerts as a


def idea(idea_id, author):
    return {'image_url': idea_id, 'name': 'Test idea',
            'chart_url': 'https://www.tradingview.com/chart/ETHUSDT/' + idea_id + '-Test/',
            'user': {'username': author}}


class AuthorTests(unittest.TestCase):
    def test_two_authors_independent_baselines_and_new_alert(self):
        db = sqlite3.connect(':memory:')
        a.initialize(db)
        self.assertEqual(a.discover(db, set(), fetch=lambda: [idea('ALTS0001', 'Altsignals')]), 1)
        self.assertEqual(a.discover(db, set(), fetch=lambda: [idea('COIN0001', 'coinpediamarkets')], author_name='coinpediamarkets'), 1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM tv_alert_outbox').fetchone()[0], 0)
        self.assertEqual(a.discover(db, set(), fetch=lambda: [idea('COIN0001','coinpediamarkets'), idea('COIN0002','coinpediamarkets')], author_name='coinpediamarkets'), 1)
        self.assertEqual(a.discover(db, set(), fetch=lambda: [idea('COIN0001','coinpediamarkets'), idea('COIN0002','coinpediamarkets')], author_name='coinpediamarkets'), 0)
        rows=db.execute('SELECT alert_key,text FROM tv_alert_outbox').fetchall()
        self.assertEqual(len(rows),1)
        self.assertIn('coinpediamarkets', rows[0][1])
        self.assertEqual(db.execute('SELECT author FROM tv_discovered_author WHERE idea_id=?', ('COIN0002',)).fetchone()[0], 'coinpediamarkets')
        db.close()

    def test_author_validation(self):
        self.assertIsNone(a.unpack(idea('COIN0001','Altsignals'), author_name='coinpediamarkets'))
        self.assertIsNotNone(a.unpack(idea('COIN0001','coinpediamarkets'), author_name='coinpediamarkets'))


if __name__ == '__main__':
    unittest.main()
