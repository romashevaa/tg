import sqlite3
import tempfile
import unittest

import tv_author_settings as settings


class AuthorSettingsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')

    def tearDown(self):
        self.db.close()

    def test_default_migration_idempotent(self):
        settings.initialize(self.db)
        settings.initialize(self.db)
        self.assertEqual(len(settings.list_authors(self.db)), 2)
        self.assertEqual(len(settings.enabled_authors(self.db)), 2)

    def test_can_add_many_authors_and_disable(self):
        for n in range(80):
            settings.set_enabled(self.db, f'testuser{n}', True)
        self.assertEqual(len(settings.enabled_authors(self.db)), 82)
        settings.set_enabled(self.db, 'https://www.tradingview.com/u/testuser10/', False)
        self.assertEqual(len(settings.enabled_authors(self.db)), 81)
        settings.set_enabled(self.db, 'testuser10', True)
        self.assertEqual(len(settings.enabled_authors(self.db)), 82)

    def test_state_persists_in_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = folder + '/db.sqlite'
            with sqlite3.connect(path) as db:
                settings.set_enabled(db, 'extraauthor', True)
            with sqlite3.connect(path) as db:
                self.assertIn('extraauthor', settings.enabled_authors(db))


if __name__ == '__main__':
    unittest.main()
