"""Conservative, read-only replay of historical original TradingView trade ideas.

No order API calls. A reached level is not evidence of an actual exchange fill.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite

STEP = 300_000  # 5-minute candles
MAX_DAYS = 7

def replay(sig, rows, publish_ms, *, end_ms=None):
    """Return evidence about hypothetical entry and level touches, never claim actual profits."""
    entry = sig.entry_low if sig.entry_low is not None else sig.entry_high
    sl = sig.stop_loss
    tps = list(sig.take_profits or [])
    if sig.side not in ('long','short') or entry is None or sl is None or not tps:
        return {'status':'INCOMPLETE', 'detail':'Недостатньо Entry / SL / TP'}
    long = sig.side == 'long'
    if entry <= 0 or (sl >= entry if long else sl <= entry) or any((tp <= entry if long else tp >= entry) for tp in tps):
        return {'status':'INVALID', 'detail':'Некоректний порядок рівнів'}
    # Use only full candles after publication; do not use the candle containing the post.
    eligible = [r for r in sorted(rows) if r[0] >= publish_ms and (end_ms is None or r[0]+STEP <= end_ms)
                and len(r)>=5 and all(isfinite(float(x)) and x>0 for x in r[1:5])]
    if not eligible or eligible[0][0] > publish_ms + 3*STEP:
        return {'status':'NO_DATA', 'detail':'Немає свічок від моменту публікації'}
    entry_kind = sig.entry_type or 'none'
    if entry_kind not in ('market', 'limit'):
        from types import SimpleNamespace
        outcomes = {}
        for assumed in ('market', 'limit'):
            clone = SimpleNamespace(side=sig.side, entry_low=sig.entry_low,
                                    entry_high=sig.entry_high, stop_loss=sig.stop_loss,
                                    take_profits=sig.take_profits, entry_type=assumed)
            outcomes[assumed] = replay(clone, rows, publish_ms, end_ms=end_ms)
        return {'status': 'ENTRY_UNCERTAIN',
                'detail': 'Тип входу не заданий автором; MARKET і LIMIT змодельовані окремо',
                'alternatives': outcomes}
    # Immediate entry: first candle after publication, but published numeric entry may differ
    # from actual quote. Do not assume a historical market fill at an idealized author level.
    if entry_kind == 'market':
        actual = eligible[0][1]
        drift = abs(actual-entry)/entry
        if drift > .01:
            return {'status':'ENTRY_MISMATCH', 'detail':f'Ринкова ціна після поста відрізнялась від Entry на {drift:.1%}; вхід не доведено'}
        fill = 0
    elif entry_kind == 'limit':
        # BUY limit is marketable at open <= limit, SELL limit at open >= limit.
        # Such an order may execute immediately at the opening quote, rather than being invalid.
        marketable_at_open = eligible[0][1] <= entry if long else eligible[0][1] >= entry
        end_entry = eligible[0][0] + 24*3_600_000
        fill = (0 if marketable_at_open else next(
            (i for i,r in enumerate(eligible) if r[0] <= end_entry and
             (r[3] <= entry if long else r[2] >= entry)), None))
        if fill is None:
            return {'status':'NO_FILL', 'detail':'LIMIT не торкнувся Entry протягом 24 год'}
    hit = [False]*len(tps)
    if entry_kind == 'limit' and marketable_at_open:
        # A marketable limit can fill at the first candle's opening quote, but
        # only when the quote has not already crossed a stop or all targets.
        open_quote = eligible[0][1]
        if (open_quote <= sl if long else open_quote >= sl) or (open_quote >= min(tps) if long else open_quote <= max(tps)):
            return {'status':'ENTRY_UNCERTAIN', 'detail':'Перший доступний open уже за SL/TP; виконання та порядок подій невідомі'}
    # Same-candle stop / take ambiguity is explicitly unresolved. On the candle
    # of a limit fill, we cannot infer whether SL/TP happened before fill.
    for i in range(fill,len(eligible)):
        r=eligible[i]
        stop = r[3]<=sl if long else r[2]>=sl
        targets = [r[2]>=tp if long else r[3]<=tp for tp in tps]
        if i==fill and entry_kind=='limit' and (stop or any(targets)):
            return {'status':'AMBIGUOUS','detail':'У свічці входу є дотик до SL/TP; порядок невідомий'}
        if stop and any(targets):
            return {'status':'AMBIGUOUS','detail':'SL і TP у тій самій 5m-свічці; порядок невідомий', 'tp_hit':sum(hit)}
        if stop:
            return {'status':'SL', 'detail':f'SL до завершення всіх TP; до цього TP: {sum(hit)}/{len(tps)}', 'tp_hit':sum(hit)}
        for j,t in enumerate(targets):
            if t: hit[j]=True
        if all(hit):
            return {'status':'ALL_TP','detail':f'Торкання всіх {len(tps)} TP (без комісій і гарантії виконання)', 'tp_hit':len(tps)}
    return {'status':'PARTIAL' if any(hit) else 'OPEN',
            'detail':f'TP торкнулося {sum(hit)}/{len(tps)}; SL не торкнуто за доступний період', 'tp_hit':sum(hit)}

async def historical_check(prices, symbol, sig, published, now=None):
    if published is None:
        return {'status':'NO_DATE','detail':'Немає підтвердженої дати оригінальної публікації'}
    now = now or datetime.now(timezone.utc)
    if published.tzinfo is None: published=published.replace(tzinfo=timezone.utc)
    if published >= now:
        return {'status':'NO_DATE','detail':'Дата ідеї не в минулому'}
    if sig.kind!='new_signal' or not symbol:
        return {'status':'NOT_SIGNAL','detail':'Немає початкового сигналу або торгової пари'}
    start=int(published.timestamp()*1000)
    end=int(min(published+timedelta(days=MAX_DAYS),now).timestamp()*1000)
    # start at first full 5m candle strictly following post.
    first=(start//STEP+1)*STEP
    try:
        rows, source = await prices.candles(symbol,first,end,'5m')
    except Exception as exc:
        return {'status':'NO_DATA','detail':f'Свічки недоступні: {type(exc).__name__}'}
    if not rows:
        return {'status':'NO_DATA','detail':'Біржа не повернула історичних свічок'}
    result=replay(sig,rows,first,end_ms=end)
    result['source']=source
    result['timeframe']='5m'
    result['horizon_days']=MAX_DAYS
    if rows[-1][0]+STEP < end-2*STEP and result['status'] in ('OPEN','PARTIAL','NO_FILL'):
        result={'status':'INCOMPLETE_DATA','detail':'Історія свічок обірвана до завершення перевірки', 'source':source}
    return result
