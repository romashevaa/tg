"""Monthly breakdown for the channel replay, using the same simulated fills as /eval.

Totals are normalized at 1% starting equity risk per trade (not publisher PnL).
"""
from collections import defaultdict
from datetime import datetime, timezone


def monthly_rows(trades, published_dates, start, end):
    """Use the FIRST take-profit exit strategy, avoiding lookahead best-of-strategies."""
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f'{y:04d}-{m:02d}')
        m += 1
        if m == 13: y, m = y + 1, 1
    buckets = {k: [] for k in months}
    for tr in trades:
        dt = published_dates.get(tr.message_id)
        if dt is not None and start <= dt <= end and dt.strftime('%Y-%m') in buckets:
            buckets[dt.strftime('%Y-%m')].append(tr)
    out = []
    for key in reversed(months):
        rows = buckets[key]
        # Expired / open trades are excluded: their R is a mark-to-market, not a closed trade.
        closed = [t for t in rows if t.status in ('stopped', 'targets') and 'first' in t.r]
        wins = sum(t.r['first'] > 0 for t in closed)
        losses = sum(t.r['first'] < 0 for t in closed)
        flat = len(closed) - wins - losses
        total_r = sum(t.r['first'] for t in closed)
        out.append(dict(month=key, signals=len(rows), wins=wins, losses=losses,
                        flat=flat, unresolved=len(rows)-len(closed),
                        win_rate=100*wins/(wins+losses) if wins+losses else None,
                        total_r=total_r, total_pct_1risk=total_r))
    return out


def format_year(title, rows, earliest, requested_start, scanned_count, latest):
    lines = [f'📊 {title} · помісячно', 'FUTURES · модель: TP1 / 1% ризику на угоду']
    for r in rows:
        if r['signals'] == 0:
            lines.append(f"\n{r['month']}: 0 розпізнаних сигналів")
            continue
        rate = f"{r['win_rate']:.1f}%" if r['win_rate'] is not None else '—'
        lines.append(f"\n📅 {r['month']} · Signals {r['signals']} · Wins {r['wins']} · Losses {r['losses']}"
                     f"\nWin rate {rate} · Total {r['total_r']:+.2f}R (~{r['total_pct_1risk']:+.2f}%*)"
                     f" · Other {r['unresolved']+r['flat']}")
    covered = earliest <= requested_start
    lines.append(f"\nПокриття: {earliest.date()} — {latest.date()} · {scanned_count} повідомлень.")
    if not covered:
        lines.append('⚠️ Неповне покриття року: ранні повідомлення не потрапили у вибірку.')
    lines.append('⚠️ Результати лише розпізнаних сигналів; частина графіків може не прочитатися.')
    lines.append('* Умовний % = сума R за ризику 1% на угоду, без складного відсотка; це НЕ дохідність автора. '
                 'Враховано модельну комісію /eval, але не гарантується виконання, проскальзування чи фактичний PnL.')
    return '\n'.join(lines)
