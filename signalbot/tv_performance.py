"""Read-only TradingView replay, 30-day TP1/SL and informational TP2/MFE/MAE.

Entry: first complete 5m candle after publication if within 1% of stated entry.
TP1 closes the entire hypothetical position. TP2 is *not* a separate fill.
Does not model exchange fills, fees, slippage or actual author's actions.
"""
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from math import isfinite

from .tv_entry import resolve_symbol

STEP = 300_000
HORIZON_DAYS = 30
HORIZON = timedelta(days=HORIZON_DAYS)


def check_first_tp(rows, published_ms, side, declared_entry, stop, tp, *, tolerance=.01,
                   end_ms=None, horizon_days=HORIZON_DAYS, tp2=None):
    if side not in ('LONG', 'SHORT'):
        return {'status':'INVALID'}
    try:
        entry, sl, target = map(float, (declared_entry, stop, tp))
        second = float(tp2) if tp2 is not None else None
    except (ValueError, TypeError):
        return {'status':'INCOMPLETE'}
    if not all(isfinite(v) and v > 0 for v in (entry,sl,target)):
        return {'status':'INVALID'}
    long = side == 'LONG'
    if (long and not sl < entry < target) or (not long and not target < entry < sl):
        return {'status':'INVALID'}
    if second is not None and (not isfinite(second) or second <= 0 or
                               (long and second <= target) or (not long and second >= target)):
        second = None
    first = (published_ms // STEP + 1) * STEP
    full_end = first + int(horizon_days * 24 * 3600_000)
    horizon = min(full_end, end_ms) if end_ms is not None else full_end
    # End is EXCLUSIVE and may only contain fully closed candles.
    good = sorted((r for r in rows if len(r)>=5 and first<=r[0]<horizon and
                   all(isfinite(float(x)) and float(x)>0 for x in r[1:5])), key=lambda r:r[0])
    if not good or good[0][0] != first:
        return {'status':'NO_DATA'}
    quote = float(good[0][1]); drift = abs(quote-entry)/entry
    if drift > tolerance:
        return {'status':'ENTRY_MISMATCH','drift_pct':round(drift*100,3)}
    if (long and not sl < quote < target) or (not long and not target < quote < sl):
        return {'status':'ENTRY_UNCERTAIN'}
    risk = abs(quote-sl)
    max_favorable = max_adverse = 0.0
    tp2_touched = False
    previous = None
    for r in good:
        timestamp,_,hi,lo,_ = r[:5]
        # Missing candle before resolution prevents a reliable WIN/LOSS conclusion.
        if previous is not None and timestamp != previous + STEP:
            return {'status':'INCOMPLETE_DATA','entry_open':quote}
        previous=timestamp
        high,low=float(hi),float(lo)
        favorable=(high-quote if long else quote-low)/risk
        adverse=(quote-low if long else high-quote)/risk
        max_favorable=max(max_favorable,favorable)
        max_adverse=max(max_adverse,adverse)
        if second is not None and (high>=second if long else low<=second):
            tp2_touched=True
        hit_stop=(low<=sl if long else high>=sl)
        hit_target=(high>=target if long else low<=target)
        metadata={'entry_open':quote,'mfe_r':round(max_favorable,3),
                  'mae_r':round(max_adverse,3),'tp2_touched':tp2_touched,
                  'exit_candle_ms':timestamp}
        if hit_stop and hit_target:
            return {'status':'AMBIGUOUS',**metadata}
        if hit_stop:
            return {'status':'LOSS','r':-1.0,**metadata}
        if hit_target:
            reward=(target-quote if long else quote-target)
            return {'status':'WIN','r':round(reward/risk,4),**metadata}
    metadata={'entry_open':quote,'mfe_r':round(max_favorable,3),
              'mae_r':round(max_adverse,3),'tp2_touched':tp2_touched}
    last_required = horizon - STEP
    if good[-1][0] < last_required or len(good) < int((horizon-first)//STEP):
        return {'status':'INCOMPLETE_DATA',**metadata}
    if horizon < full_end:
        return {'status':'OPEN',**metadata}
    return {'status':'TIMEOUT',**metadata}


async def evaluate(prices, record, *, now=None, horizon_days=HORIZON_DAYS):
    c=record.get('classification') or {}; ai=record.get('ai') or {}
    if c.get('intent')!='DIRECT_SIGNAL':return {'status':'NOT_SIGNAL'}
    symbol=resolve_symbol(record)
    if not symbol:return {'status':'NO_SYMBOL'}
    stamp=record.get('published_at')
    if not stamp:return {'status':'NO_DATE'}
    try:
        dt=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError):return {'status':'NO_DATE'}
    now=now or datetime.now(timezone.utc)
    if dt>=now:return {'status':'NO_DATE'}
    entry=ai.get('entry_low') if ai.get('entry_low') is not None else ai.get('entry_high')
    tps=ai.get('take_profits') or []
    if not tps:return {'status':'INCOMPLETE'}
    published_ms=int(dt.timestamp()*1000)
    first=(published_ms//STEP+1)*STEP
    # A five-minute candle is usable only after closing.
    closed_end=int(now.timestamp()*1000)//STEP*STEP
    end=min(first+int(horizon_days*24*3600_000),closed_end)
    if end<=first:return {'status':'NO_DATA'}
    try:rows,source=await prices.candles(symbol,first,end-1,'5m')
    except Exception:return {'status':'NO_DATA'}
    result=check_first_tp(rows,published_ms,c.get('side'),entry,ai.get('stop_loss'),
                          tps[0],tp2=tps[1] if len(tps)>1 else None,
                          end_ms=end,horizon_days=horizon_days)
    result.update(symbol=symbol,source=source,month=dt.strftime('%Y-%m'),id=record.get('idea_id'))
    return result


def summary(items):
    months=defaultdict(list)
    for item in items:months[item.get('month','unknown')].append(item)
    lines=['📊 TradingView · 5m Replay (TP1/SL, до 30 днів)',
           'Умовний вхід на open першої повної 5m свічки; весь обсяг закривається на TP1 або SL.',
           'TP2 та MFE/MAE — лише довідкові. Без комісій, проскальзування та ордерів.']
    for month,group in sorted(months.items(),reverse=True):
        counts=Counter(x['status'] for x in group)
        settled=[x for x in group if x['status'] in ('WIN','LOSS')]
        lines += ['',f'{month} · сигналів {len(group)} · WIN {counts["WIN"]} · LOSS {counts["LOSS"]} '
                 f'· OPEN {counts["OPEN"]} · TIMEOUT {counts["TIMEOUT"]} · інші {len(group)-len(settled)-counts["OPEN"]-counts["TIMEOUT"]}',
                 f'Win Rate: {counts["WIN"]/len(settled):.1%} (лише завершені)' if settled else 'Win Rate: — (немає завершених)',
                 f'Total R (TP1): {sum(x["r"] for x in settled):+.2f}R' if settled else 'Total R (TP1): —']
        for x in group:
            extra=f" {x['r']:+.2f}R" if 'r' in x else ''
            excursions=(f" · MFE {x['mfe_r']:.2f}R / MAE {x['mae_r']:.2f}R" if 'mfe_r' in x else '')
            tp2=(' · TP2 touched*' if x.get('tp2_touched') else '')
            lines.append(f"• {x.get('symbol','?')} {x.get('id','')} · {x['status']}{extra}{excursions}{tp2}")
    lines.append('*TP2 touched — у спостережуваному фрагменті до TP1/SL; це не друга закрита угода.')
    return '\n'.join(lines)
