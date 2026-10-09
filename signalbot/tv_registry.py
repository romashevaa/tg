"""Opt-in TradingView idea registry. No profile scraping, polling or order execution."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

USER_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
IDEA_RE = re.compile(r"^/chart/[^/]+/([A-Za-z0-9]{8})(?:-[^/]*)?/?$")
PROFILE_RE = re.compile(r"^/u/([A-Za-z0-9_-]{2,64})/?$")


def parse_profile(value: str) -> tuple[str, str]:
    raw = value.strip()
    if raw.startswith('@'): raw = raw[1:]
    if USER_RE.fullmatch(raw):
        return raw.lower(), f"https://www.tradingview.com/u/{raw}/"
    u = urlparse(raw)
    if u.scheme != 'https' or u.hostname not in ('tradingview.com','www.tradingview.com'):
        raise ValueError('Потрібне посилання https://www.tradingview.com/u/username/')
    m = PROFILE_RE.fullmatch(u.path)
    if not m: raise ValueError('Це не TradingView-профіль (/u/username/)')
    name = m.group(1)
    return name.lower(), f"https://www.tradingview.com/u/{name}/"


def parse_idea(value: str) -> tuple[str, str]:
    u = urlparse(value.strip())
    if u.scheme != 'https' or u.hostname not in ('tradingview.com','www.tradingview.com'):
        raise ValueError('Потрібен https TradingView URL на опубліковану ідею')
    m=IDEA_RE.fullmatch(u.path)
    if not m: raise ValueError('Потрібна TradingView ідея /chart/SYMBOL/IDEA-ID-title/')
    return m.group(1), f"https://www.tradingview.com{u.path.rstrip('/')}/"

class TvRegistry:
    def __init__(self, db):
        self.db=db
        self.db.executescript("""CREATE TABLE IF NOT EXISTS tv_authors (
            handle TEXT PRIMARY KEY, url TEXT NOT NULL, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tv_ideas (
            idea_id TEXT PRIMARY KEY, handle TEXT NOT NULL, url TEXT NOT NULL,
            added_at TEXT NOT NULL, FOREIGN KEY(handle) REFERENCES tv_authors(handle)
        );
        CREATE TABLE IF NOT EXISTS tv_scan_results (
          idea_id TEXT PRIMARY KEY, report TEXT NOT NULL, analyzed_at TEXT NOT NULL,
          FOREIGN KEY(idea_id) REFERENCES tv_ideas(idea_id)
        );""")

    def ensure_metadata(self):
        self.db.execute("""CREATE TABLE IF NOT EXISTS tv_idea_metadata (
          idea_id TEXT PRIMARY KEY, published_at TEXT, title TEXT, market TEXT,
          source_author TEXT, fetched_at TEXT NOT NULL)""")
        self.db.commit()

    def save_metadata(self, iid, published, title='', market='', source_author=''):
        self.ensure_metadata()
        self.db.execute("""INSERT INTO tv_idea_metadata VALUES(?,?,?,?,?,?)
          ON CONFLICT(idea_id) DO UPDATE SET
          published_at=excluded.published_at,title=excluded.title,
          market=excluded.market,source_author=excluded.source_author,
          fetched_at=excluded.fetched_at""",
          (iid, published.isoformat() if published else None, title, market,
           source_author, datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def pending(self, handle, limit=20):
        self.ensure_metadata()
        handle=parse_profile(handle)[0]
        return self.db.execute("""SELECT i.idea_id,i.url,m.published_at
          FROM tv_ideas i LEFT JOIN tv_idea_metadata m ON m.idea_id=i.idea_id
          LEFT JOIN tv_scan_results r ON r.idea_id=i.idea_id
          WHERE i.handle=? AND r.idea_id IS NULL
          ORDER BY COALESCE(m.published_at, i.added_at) DESC,i.idea_id DESC LIMIT ?""",
          (handle,limit)).fetchall()

    def audit_status(self, handle):
        self.ensure_metadata()
        handle=parse_profile(handle)[0]
        return self.db.execute("""SELECT COUNT(*) AS stored,
          SUM(CASE WHEN m.published_at IS NOT NULL THEN 1 ELSE 0 END) AS dated,
          SUM(CASE WHEN r.idea_id IS NOT NULL THEN 1 ELSE 0 END) AS analyzed,
          MIN(m.published_at) AS earliest,MAX(m.published_at) AS latest
          FROM tv_ideas i LEFT JOIN tv_idea_metadata m ON i.idea_id=m.idea_id
          LEFT JOIN tv_scan_results r ON r.idea_id=i.idea_id WHERE i.handle=?""", (handle,)).fetchone()

    def monthly_overview(self,handle):
        self.ensure_metadata()
        handle=parse_profile(handle)[0]
        return self.db.execute("""SELECT substr(m.published_at,1,7) month,
          COUNT(*) ideas, COUNT(r.idea_id) analyzed,
          SUM(CASE WHEN r.report LIKE '%· СИГНАЛ%' OR r.report LIKE '%· SIGNAL%' THEN 1 ELSE 0 END) signals
          FROM tv_ideas i JOIN tv_idea_metadata m ON i.idea_id=m.idea_id
          LEFT JOIN tv_scan_results r ON i.idea_id=r.idea_id
          WHERE i.handle=? AND m.published_at IS NOT NULL
          GROUP BY substr(m.published_at,1,7) ORDER BY month DESC""", (handle,)).fetchall()

    def ensure_metadata(self):
        self.db.execute("""CREATE TABLE IF NOT EXISTS tv_idea_metadata (
          idea_id TEXT PRIMARY KEY, published_at TEXT, title TEXT, market TEXT,
          source_author TEXT, fetched_at TEXT NOT NULL)""")
        self.db.commit()

    def save_metadata(self, iid, published, title='', market='', source_author=''):
        self.ensure_metadata()
        self.db.execute("""INSERT INTO tv_idea_metadata VALUES(?,?,?,?,?,?)
          ON CONFLICT(idea_id) DO UPDATE SET
          published_at=excluded.published_at,title=excluded.title,
          market=excluded.market,source_author=excluded.source_author,
          fetched_at=excluded.fetched_at""",
          (iid, published.isoformat() if published else None, title, market,
           source_author, datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def pending(self, handle, limit=20):
        self.ensure_metadata()
        handle=parse_profile(handle)[0]
        return self.db.execute("""SELECT i.idea_id,i.url,m.published_at
          FROM tv_ideas i LEFT JOIN tv_idea_metadata m ON m.idea_id=i.idea_id
          LEFT JOIN tv_scan_results r ON r.idea_id=i.idea_id
          WHERE i.handle=? AND r.idea_id IS NULL
          ORDER BY COALESCE(m.published_at, i.added_at) DESC,i.idea_id DESC LIMIT ?""",
          (handle,limit)).fetchall()

    def audit_status(self, handle):
        self.ensure_metadata()
        handle=parse_profile(handle)[0]
        return self.db.execute("""SELECT COUNT(*) AS stored,
          SUM(CASE WHEN m.published_at IS NOT NULL THEN 1 ELSE 0 END) AS dated,
          SUM(CASE WHEN r.idea_id IS NOT NULL THEN 1 ELSE 0 END) AS analyzed,
          MIN(m.published_at) AS earliest,MAX(m.published_at) AS latest
          FROM tv_ideas i LEFT JOIN tv_idea_metadata m ON i.idea_id=m.idea_id
          LEFT JOIN tv_scan_results r ON r.idea_id=i.idea_id WHERE i.handle=?""", (handle,)).fetchone()

    def monthly_overview(self,handle):
        self.ensure_metadata()
        handle=parse_profile(handle)[0]
        return self.db.execute("""SELECT substr(m.published_at,1,7) month,
          COUNT(*) ideas, COUNT(r.idea_id) analyzed,
          SUM(CASE WHEN r.report LIKE '%· СИГНАЛ%' OR r.report LIKE '%· SIGNAL%' THEN 1 ELSE 0 END) signals
          FROM tv_ideas i JOIN tv_idea_metadata m ON i.idea_id=m.idea_id
          LEFT JOIN tv_scan_results r ON i.idea_id=r.idea_id
          WHERE i.handle=? AND m.published_at IS NOT NULL
          GROUP BY substr(m.published_at,1,7) ORDER BY month DESC""", (handle,)).fetchall()

    def add_author(self, value):
        handle,url=parse_profile(value)
        self.db.execute('INSERT OR IGNORE INTO tv_authors VALUES(?,?,?)',
                        (handle,url,datetime.now(timezone.utc).isoformat()))
        self.db.commit()
        return handle

    def authors(self):
        return self.db.execute("""SELECT a.handle, a.url, COUNT(i.idea_id) AS count
             FROM tv_authors a LEFT JOIN tv_ideas i ON i.handle=a.handle
             GROUP BY a.handle ORDER BY a.handle""").fetchall()

    def add_idea(self, handle, url):
        handle=parse_profile(handle)[0]
        if not self.db.execute('SELECT 1 FROM tv_authors WHERE handle=?',(handle,)).fetchone():
            raise ValueError('Спершу додай автора через /tvadd')
        iid,url=parse_idea(url)
        old=self.db.execute('SELECT handle FROM tv_ideas WHERE idea_id=?',(iid,)).fetchone()
        if old:
            return iid, False, old['handle']
        self.db.execute('INSERT INTO tv_ideas VALUES(?,?,?,?)',
                        (iid,handle,url,datetime.now(timezone.utc).isoformat()))
        self.db.commit()
        return iid, True, handle

    def ideas(self, handle):
        handle=parse_profile(handle)[0]
        return self.db.execute('SELECT idea_id,url FROM tv_ideas WHERE handle=? ORDER BY added_at DESC',(handle,)).fetchall()

    def save_scan_report(self, idea_id, report):
        self.db.execute('INSERT OR REPLACE INTO tv_scan_results VALUES (?,?,?)',
                        (idea_id,report,datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def scan_reports(self, handle):
        handle=parse_profile(handle)[0]
        return self.db.execute('SELECT i.idea_id,i.url,r.report FROM tv_ideas i JOIN tv_scan_results r ON i.idea_id=r.idea_id WHERE i.handle=? ORDER BY r.analyzed_at DESC',(handle,)).fetchall()

    def audit_page(self, handle, page=1, per_page=5):
        self.ensure_metadata()
        handle=parse_profile(handle)[0]
        total=self.db.execute('SELECT COUNT(*) FROM tv_ideas WHERE handle=?',(handle,)).fetchone()[0]
        rows=self.db.execute("""SELECT i.idea_id,i.url,m.title,m.published_at,r.report
            FROM tv_ideas i LEFT JOIN tv_idea_metadata m ON m.idea_id=i.idea_id
            LEFT JOIN tv_scan_results r ON r.idea_id=i.idea_id
            WHERE i.handle=? ORDER BY COALESCE(m.published_at,i.added_at) DESC,i.idea_id DESC
            LIMIT ? OFFSET ?""",(handle,per_page,(page-1)*per_page)).fetchall()
        return total,rows

    def audit_idea(self, idea_id):
        self.ensure_metadata()
        return self.db.execute("""SELECT i.idea_id,i.handle,i.url,m.title,m.published_at,r.report
            FROM tv_ideas i LEFT JOIN tv_idea_metadata m ON m.idea_id=i.idea_id
            LEFT JOIN tv_scan_results r ON r.idea_id=i.idea_id
            WHERE i.idea_id=?""",(idea_id,)).fetchone()

    def ensure_reviews(self):
        self.db.execute("""CREATE TABLE IF NOT EXISTS tv_second_reviews (
          idea_id TEXT PRIMARY KEY, review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS tv_v26_reviews (
          idea_id TEXT PRIMARY KEY, review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS tv_v27_reviews (
          idea_id TEXT PRIMARY KEY, review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL)""")
        self.db.commit()

    def pending_reviews(self, handle, limit=10):
        self.ensure_reviews();handle=parse_profile(handle)[0]
        return self.db.execute("""SELECT i.idea_id,i.url FROM tv_ideas i
          LEFT JOIN tv_v27_reviews r ON r.idea_id=i.idea_id
          WHERE i.handle=? AND r.idea_id IS NULL ORDER BY i.added_at DESC LIMIT ?""", (handle,limit)).fetchall()

    def save_review(self, iid, data):
        import json
        self.ensure_reviews()
        self.db.execute("INSERT OR REPLACE INTO tv_v27_reviews VALUES (?,?,?)",
                        (iid,json.dumps(data,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def get_review(self, iid):
        import json
        self.ensure_reviews()
        row=self.db.execute("SELECT review_json FROM tv_v27_reviews WHERE idea_id=?",(iid,)).fetchone()
        if not row:
            row=self.db.execute("SELECT review_json FROM tv_v26_reviews WHERE idea_id=?",(iid,)).fetchone()
        if not row:
            row=self.db.execute("SELECT review_json FROM tv_second_reviews WHERE idea_id=?",(iid,)).fetchone()
        return json.loads(row[0]) if row else None

    def review_stats(self, handle):
        self.ensure_reviews();handle=parse_profile(handle)[0]
        total=self.db.execute('SELECT COUNT(*) FROM tv_ideas WHERE handle=?',(handle,)).fetchone()[0]
        rows=self.db.execute("""SELECT r.review_json FROM tv_v27_reviews r JOIN tv_ideas i
          ON r.idea_id=i.idea_id WHERE i.handle=?""",(handle,)).fetchall()
        import json
        counts={}
        for row in rows:
            label=json.loads(row[0])['classification']['intent']
            counts[label]=counts.get(label,0)+1
        return total,len(rows),counts
