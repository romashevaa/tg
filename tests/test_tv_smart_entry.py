import unittest
from types import SimpleNamespace
from tv_smart_entry import review_candles


def ev(**kwargs):
    data = dict(symbol='BTCUSD', direction='LONG', entry=100, stop_loss=95, targets=[110], timeframe='15m')
    data.update(kwargs)
    return SimpleNamespace(**data)


class SmartEntryTests(unittest.TestCase):
    def setUp(self):
        self.span = 900000
        self.now = 9 * self.span

    def bars(self, recent):
        seq = [(i * self.span, o, h, l, c) for i, (o, h, l, c) in enumerate(recent, 4)]
        return seq

    def test_waiting(self):
        rows = self.bars([(102,103,101,102)]*4)
        self.assertEqual(review_candles(ev(), rows, self.now, interval='15m').status, 'WAITING')

    def test_confirming(self):
        rows = self.bars([(101,102,99.9,100)]*4)
        self.assertEqual(review_candles(ev(), rows, self.now, interval='15m').status, 'CONFIRMING')

    def test_ready_review(self):
        rows = self.bars([(101,102,99,101), (100,101,99,100), (100,100.1,99,99.9), (99.8,100.2,99.7,100.1)])
        self.assertEqual(review_candles(ev(), rows, self.now, interval='15m').status, 'READY_REVIEW')

    def test_invalid_stop(self):
        rows = self.bars([(94,95,93,94)]*4)
        self.assertEqual(review_candles(ev(), rows, self.now, interval='15m').status, 'INVALID')

    def test_ignore_unclosed_candle(self):
        rows = self.bars([(102,103,101,102)]*4)
        rows.append((self.now-self.span//2, 99, 101, 98, 100))
        self.assertEqual(review_candles(ev(), rows, self.now, interval='15m').status, 'WAITING')

    def test_stale_data(self):
        rows = [(i*self.span, 100, 101, 99, 100) for i in range(4)]
        self.assertEqual(review_candles(ev(), rows, self.now, interval='15m').status, 'CHECK')


if __name__ == '__main__': unittest.main()
