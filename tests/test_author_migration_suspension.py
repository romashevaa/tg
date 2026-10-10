import sqlite3
import unittest
import tv_author_settings as a

class TestLegacyMigration(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE tv_authors (handle TEXT PRIMARY KEY, url TEXT, added_at TEXT)')
        for name in ('KennyYenKen', 'ExuberanceTrading', 'blackswanvii'):
            self.db.execute('INSERT INTO tv_authors VALUES (?,?,?)', (name, '', ''))

    def tearDown(self):
        self.db.close()

    def test_old_authors_become_monitored(self):
        a.initialize(self.db)
        self.assertTrue({'kennyyenken','exuberancetrading','blackswanvii'} <= set(map(str.lower,a.enabled_authors(self.db))))

    def test_suspension_and_recheck_after_12_hours(self):
        a.initialize(self.db)
        notice = 'Altsignals got suspended for bad behavior. House Rules'
        self.assertFalse(a.check_author(self.db, 'Altsignals', fetch=lambda url: notice, current_time=100))
        def fail(_):
            raise AssertionError('should be deferred')
        self.assertFalse(a.check_author(self.db, 'Altsignals', fetch=fail, current_time=101))
        self.assertTrue(a.check_author(self.db, 'Altsignals', fetch=lambda url: '<html>active</html>', current_time=100+43200))
        self.assertIsNone(a.suspension_info(self.db, 'Altsignals'))

    def test_no_suspension_on_other_errors(self):
        a.initialize(self.db)
        self.assertTrue(a.check_author(self.db, 'KennyYenKen', fetch=lambda url: 'regular tradingview page', current_time=100))
