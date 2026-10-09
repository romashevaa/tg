"""Read-only triage of non-direct TradingView ideas from stored v2.7 reviews.

Candidate flags are heuristics, never independent confirmation of a signal.
"""
import json
import re

ENTRY = re.compile(r'\b(?:entry|enter|buy(?:ing)?|sell(?:ing)?|short(?:ing)?|long(?:ing)?|stop\s*loss|\bSL\s*[:=]|\bTP\s*\d*\s*[:=])\b', re.I)
LEVEL = re.compile(r'\b(?:\d+(?:[.,]\d+)?\s*(?:-|–|to)\s*\d+(?:[.,]\d+)?|\d+[.,]\d{2,})\b')
RESULT = re.compile(r'\b(?:tp\d?\s+hit|target\s+hit|closed\s+(?:the\s+)?trade|profit\s+taken|as\s+predicted)\b', re.I)


def triage(review):
    c=review.get('classification') or {}
    ai=review.get('ai') or {}
    src=review.get('source_excerpt') or ''
    flags=[]
    if ai.get('entry_low') is not None: flags.append('ENTRY_LEVEL')
    if ai.get('stop_loss') is not None: flags.append('SL_LEVEL')
    if ai.get('take_profits'): flags.append('TP_LEVEL')
    if ENTRY.search(src): flags.append('TRADE_LANGUAGE')
    if LEVEL.search(src): flags.append('PRICE_LEVELS')
    if review.get('image_loaded'): flags.append('CHART_AVAILABLE')
    if RESULT.search(src): flags.append('POSSIBLE_RESULT')
    n=sum(f in flags for f in ('ENTRY_LEVEL','SL_LEVEL','TP_LEVEL'))
    if c.get('intent') == 'CONDITIONAL_SETUP':
        priority='CHECK_TRIGGER'
    elif n >= 2 or (n >= 1 and 'TRADE_LANGUAGE' in flags):
        priority='HIGH_REVIEW'
    elif 'TRADE_LANGUAGE' in flags and 'PRICE_LEVELS' in flags:
        priority='MEDIUM_REVIEW'
    else: priority='LOW_REVIEW'
    if 'POSSIBLE_RESULT' in flags and n == 0: priority='POSSIBLE_UPDATE'
    return {'priority':priority, 'flags': flags, 'missing': [label for key,label in [('entry_low','ENTRY'),('stop_loss','SL'),('take_profits','TP')] if not ai.get(key)], 'original_intent':c.get('intent','UNKNOWN')}


def skipped_rows(db,handle):
    rows=db.execute('''SELECT r.idea_id, r.review_json, i.url
       FROM tv_v27_reviews r JOIN tv_ideas i ON i.idea_id=r.idea_id
       WHERE i.handle=? ORDER BY i.added_at DESC''',(handle,)).fetchall()
    out=[]
    for row in rows:
        try: d=json.loads(row['review_json'])
        except (ValueError,TypeError):continue
        if (d.get('classification') or {}).get('intent')=='DIRECT_SIGNAL': continue
        t=triage(d)
        out.append({'idea_id':row['idea_id'], 'data':d, 'triage':t, 'url':row['url']})
    order={'HIGH_REVIEW':0,'CHECK_TRIGGER':1,'MEDIUM_REVIEW':2,'POSSIBLE_UPDATE':3,'LOW_REVIEW':4}
    out.sort(key=lambda r:(order.get(r['triage']['priority'],9),r['idea_id']))
    return out
