"""Monthly summaries of *audited* original TradingView ideas (not channel-wide claims).

Read-only with respect to trading. SQLite writes only an idempotent audit ledger.
"""
from __future__ import annotations
from datetime import datetime, timezone
import re

KEY_RE = re.compile(r'/chart/[^/]+/([A-Za-z0-9]{8})(?:-|/|$)', re.I)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS historical_audit_ledger (
        idea_key TEXT PRIMARY KEY, idea_url TEXT NOT NULL, published_utc TEXT NOT NULL,
        symbol TEXT NOT NULL, direction TEXT NOT NULL, entry_type TEXT NOT NULL,
        outcome TEXT NOT NULL, r_net REAL, source TEXT NOT NULL, audited_utc TEXT NOT NULL)''')
    db.commit()


def _r(sig, result):
    """Return R only for single-TP definitive exits under known idealized entry.

    No assumptions about allocation across multiple TP or intra-bar fills.
    """
    if sig.entry_low is None or sig.entry_high is None or sig.entry_low != sig.entry_high:
        return None
    if not sig.stop_loss or not sig.take_profits or len(sig.take_profits) != 1:
        return None
    entry = sig.entry_low
    risk = abs(entry - sig.stop_loss)
    if risk <= 0:
        return None
    status = result.get('status')
    if status == 'SL' and result.get('tp_hit', 0) == 0:
        return -1.0
    if status == 'ALL_TP':
        return abs(sig.take_profits[0] - entry) / risk
    return None


def record(db, url, published, sig, result):
    if not published or sig.kind != 'new_signal' or not result or not url:
        return False
    match = KEY_RE.search(url)
    if not match:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    initialize(db)
    key = match.group(1).lower()
    outcome = result.get('status', 'UNKNOWN')
    # Do not overwrite a valid previously recorded replay with transient NO_DATA.
    previous = db.execute('SELECT outcome FROM historical_audit_ledger WHERE idea_key=?', (key,)).fetchone()
    if previous and outcome in ('NO_DATA', 'NO_DATE', 'INCOMPLETE_DATA'):
        return False
    db.execute('''INSERT INTO historical_audit_ledger
       (idea_key, idea_url, published_utc, symbol, direction, entry_type,
        outcome, r_net, source, audited_utc) VALUES (?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(idea_key) DO UPDATE SET
       idea_url=excluded.idea_url, published_utc=excluded.published_utc,
       symbol=excluded.symbol, direction=excluded.direction,
       entry_type=excluded.entry_type, outcome=excluded.outcome,
       r_net=excluded.r_net, source=excluded.source,
       audited_utc=excluded.audited_utc''',
       (key, url, published.astimezone(timezone.utc).isoformat(),
        (sig.base_asset or '') + (sig.quote_asset or 'USDT'), sig.side,
        sig.entry_type or 'unknown', outcome, _r(sig, result),
        result.get('source') or 'unknown', datetime.now(timezone.utc).isoformat()))
    db.commit()
    return True


def summary(db, year_month):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', year_month):
        raise ValueError('Використання: /monthstats 2026-10')
    initialize(db)
    rows = db.execute('SELECT * FROM historical_audit_ledger WHERE substr(published_utc,1,7)=? ORDER BY published_utc', (year_month,)).fetchall()
    wins = [r for r in rows if r['outcome'] == 'ALL_TP']
    losses = [r for r in rows if r['outcome'] == 'SL' and r['r_net'] == -1.0]
    other = len(rows) - len(wins) - len(losses)
    resolved = len(wins) + len(losses)
    rate = (100 * len(wins) / resolved) if resolved else None
    known_r = [r['r_net'] for r in rows if r['r_net'] is not None]
    # 1% risk sizing example: each +/-1R corresponds to +/-1% of starting capital,
    # no compounding and before all fees. NOT the publisher's 'Total %'.
    percentage = sum(known_r) if known_r else None
    pretty = datetime.strptime(year_month, '%Y-%m').strftime('%B %Y')
    lines = [f'📊 FUTURES {pretty}', 'Перевірені TradingView-ідеї (не весь канал)',
             f'Signals: {len(rows)}', f'Wins: {len(wins)}',
             f'Losses: {len(losses)}', f'Other / Uncertain: {other}',
             f'Win rate: {rate:.2f}% (з визначених)' if rate is not None else 'Win rate: —',
             f'Total: {sum(known_r):+.2f}R (оцінено {len(known_r)} угод)' if known_r else 'Total: — (немає однозначних R)',
             f'За 1% ризику на угоду: {percentage:+.2f}%*' if percentage is not None else 'За 1% ризику: —',
             '* Умовно, без комісій, плеча, проскальзування чи складного відсотка.',
             'Облік за датою публікації UTC. Не є підтвердженим PnL.']
    if not rows:
        lines.append('Спочатку перевірте історичні TradingView-ідеї через /analyze; звіт не охоплює автоматично весь канал.')
    return '\n'.join(lines)
