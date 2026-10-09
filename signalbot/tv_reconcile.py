"""Read-only candidate reconciliation; no new trades, AI calls, or PnL changes."""
import json
from datetime import datetime, timezone, timedelta
from .tv_entry import resolve_symbol, STEP
from . import tv_chart_audit


def candidate_rows(db, handle):
    rows=db.execute("""SELECT i.idea_id,i.url,m.published_at,m.title,r.review_json
      FROM tv_ideas i JOIN tv_v27_reviews r ON i.idea_id=r.idea_id
      LEFT JOIN tv_idea_metadata m ON m.idea_id=i.idea_id
      WHERE i.handle=? ORDER BY m.published_at ASC,i.idea_id ASC""",(handle,)).fetchall()
    all_posts=[]
    for r in rows:
        try: rev=json.loads(r['review_json'])
        except (ValueError,TypeError): continue
        evidence=tv_chart_audit.read(db,r['idea_id'])
        record={'idea_id':r['idea_id'],'url':r['url'],'published_at':r['published_at'],
                'title':r['title'],'ai':rev.get('ai') or {}, 'classification':rev.get('classification') or {},
                'evidence': evidence or {}}
        record['symbol']=resolve_symbol(record)
        record['side']=(record['classification'].get('side') or '').upper()
        all_posts.append(record)
    result=[]
    for current in all_posts:
        if current['classification'].get('intent')=='DIRECT_SIGNAL':continue
        evidence=current['evidence']; verdict=evidence.get('verdict')
        if verdict not in ('CANDIDATE','REVIEW_REQUIRED'): continue
        symbol=current['symbol']; date=current['published_at']
        previous=[]
        if date and symbol:
            previous=[p for p in all_posts if p['idea_id']!=current['idea_id'] and p['symbol']==symbol
                      and p['published_at'] and p['published_at']<date]
        # Keep explicit chart/text levels separate from model's speculative or unstated levels.
        text_level=evidence.get('text_entry')
        chart_level=evidence.get('chart_entry')
        entry=text_level if text_level is not None else chart_level
        source=('TEXT' if text_level is not None else 'CHART' if chart_level is not None else 'NONE')
        result.append({**current,'entry':entry,'entry_source':source,
            'prior':previous[-2:], 'possible_update':bool(evidence.get('previous_idea_reference')),
            'verdict':verdict,'setup':evidence.get('setup','UNCERTAIN')})
    return result

async def entry_touch(prices, record, days=7):
    """Check *touch only*, not fill. Never infer an entry from market candles."""
    if record['entry'] is None:return 'NO_EXPLICIT_ENTRY'
    if not record.get('symbol'):return 'NO_CRYPTO_SYMBOL'
    stamp=record.get('published_at')
    if not stamp:return 'NO_DATE'
    try:
        dt=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        price=float(record['entry'])
        if price<=0:return 'INVALID_ENTRY'
        begin=(int(dt.timestamp()*1000)//STEP+1)*STEP
        end=min(begin+days*86400000,int(datetime.now(timezone.utc).timestamp()*1000)//STEP*STEP)
        if end<=begin:return 'NOT_YET_OBSERVABLE'
        rows,_=await prices.candles(record['symbol'],begin,end-1,'5m')
        if not rows:return 'NO_PRICE_DATA'
        rows=sorted(r for r in rows if len(r)>=4 and begin<=r[0]<end)
        if not rows or rows[0][0]!=begin:return 'INCOMPLETE_DATA'
        prev=None
        for r in rows:
            if prev is not None and r[0]!=prev+STEP:return 'INCOMPLETE_DATA'
            prev=r[0]
            if float(r[3])<=price<=float(r[2]):return 'LEVEL_TOUCHED (not fill)'
        if rows[-1][0] < end-STEP:return 'INCOMPLETE_DATA'
        return 'NOT_TOUCHED_IN_WINDOW'
    except (ValueError,TypeError,OverflowError):return 'INVALID_DATA'
    except Exception:return 'PRICE_FETCH_FAILED'

async def reconcile(prices,db,handle,limit=30):
    all_rows=candidate_rows(db,handle)
    selected=all_rows[:limit]
    lines=[f'🔗 @{handle} · кандидати chart audit: {len(all_rows)}',
           'Тільки ідеї поза DIRECT_SIGNAL. Торкання Entry ≠ виконання ордера.',
           'Жодного нового WIN/LOSS; без Gemini й ордерів.']
    for item in selected:
        prior=item['prior']
        relation=('POSSIBLE_UPDATE' if item['possible_update'] else
                  'EARLIER_SAME_SYMBOL' if prior else 'NO_EARLIER_IN_DATASET')
        # Verify only labeled text/chart entry; no forecast-level substitutions.
        touch=await entry_touch(prices,item) if item['entry'] is not None else 'NO_EXPLICIT_ENTRY'
        entry=f"{item['entry']} ({item['entry_source']})" if item['entry'] is not None else '—'
        refs=','.join(p['idea_id'] for p in prior) if prior else '—'
        lines.append(f"• {item['idea_id']} · {item['symbol'] or '?'} {item['side'] or '?'} · {item['verdict']}/{item['setup']}\n"
                     f"  Entry {entry} · {touch}\n  Зв'язок: {relation} · попередні: {refs}\n"
                     f"  /tvchartidea {item['idea_id']}")
    if len(all_rows)>limit: lines.append(f'Показано {limit}/{len(all_rows)}; наступний запуск із більшим лімітом.')
    lines.append('Пошук попередніх ідей обмежено збереженими публікаціями, а не повною історією автора.')
    return '\n'.join(lines)
