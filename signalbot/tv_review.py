"""Read-only second-pass review of historical TradingView ideas.
Separate author intent, setup conditionality and executability. No orders.
"""
import re
from datetime import datetime, timezone
from .models import Channel, TgMessage

# Trigger language must refer to the ENTRY, not simply appear anywhere in a forecast.
TRIGGER = re.compile(
    r"\b(?:wait for|only if|on (?:a |the )?(?:breakout|break|retest|confirmation))\b"
    r"|\b(?:if|once|after|when)\b[^.!?\n]{0,55}\b(?:enter(?:ing)?|buy(?:ing)?|sell(?:ing)?|short(?:ing)?|long(?:ing)?|open(?:ing)?|entry)\b"
    r"|\b(?:enter(?:ing)?|buy(?:ing)?|sell(?:ing)?|short(?:ing)?|long(?:ing)?|open(?:ing)?)\b[^.!?\n]{0,55}\b(?:if|once|after|when)\b", re.I)

DIRECT = re.compile(r"\b(?:buy(?:ing)?|sell(?:ing)?|enter|entry|open(?:ing)?|long(?:ing)?|short(?:ing)?)\s+(?:\w+\s+){0,3}(?:now|here|at|long|short)|\b(?:coin|entry|stop.?loss|target\s*1)\s*:", re.I)
RESULT = re.compile(r"\b(?:hit\s+(?:tp|target)|tp\s*\d*\s*(?:hit|reached)|closed\s+(?:in|at)\s+profit|as predicted|since we told|told you)\b", re.I)
PRICE = r"(?:\d+(?:[.,]\d+)?)"
RETEST = re.compile(r"\b(?:retest|re-test|neckline)\b.{0,45}?\b(?:at|around|near|of)\s*\$?("+PRICE+r")",re.I)
# Do not parse the enumeration in "Target 1:" as a price.
TARGET = re.compile(r"\b(?:until|towards|toward|target(?:s)?(?:\s*(?:\d+|one|two))?\s*(?:at|around|near|:|=|\u2192))\s*\$?("+PRICE+r")\b", re.I)
MARKET_CUE = re.compile(r"\b(?:market\s+(?:entry|order|buy|sell|long|short)|enter\s+(?:now|at\s+market)|buy\s+now|sell\s+now|short\s+now|long\s+now|buying\s+here|selling\s+here|entering\s+here)\b", re.I)
LIMIT_CUE = re.compile(r"\b(?:limit\s+(?:entry|order|buy|sell|long|short)|(?:buy|sell)\s+limit|place\s+(?:a\s+)?limit|pending\s+limit)\b", re.I)
WAIT_CUE = re.compile(r"\b(?:wait\s+(?:for|until)|only\s+(?:if|after)|(?:enter|buy|sell|long|short)\s+(?:on|after)\s+(?:a\s+)?(?:retest|pullback|breakout|break))\b", re.I)


def classify(sig, text, *, image_loaded=False, source_ok=True):
    """Classification of intent is independent from entry completeness or age."""
    src=text or ''
    side=sig.side.upper()
    entry=sig.entry_low is not None and sig.entry_high is not None
    sl=sig.stop_loss is not None
    tp=bool(sig.take_profits)
    explicit=bool(DIRECT.search(src))
    trigger=bool(TRIGGER.search(src)) or sig.entry_type == 'breakout'
    # An explicit entry/SL/TP call is NOT demoted for generic 'if' or 'retest' elsewhere.
    strong_call=side in ('LONG','SHORT') and (sig.kind=='new_signal' and (explicit or (entry and sl and tp)))
    if RESULT.search(src) or sig.kind=='result': intent='RESULT'
    elif sig.kind=='update': intent='UPDATE'
    elif strong_call and not trigger: intent='DIRECT_SIGNAL'
    elif side in ('LONG','SHORT') and (trigger or explicit or entry or sig.kind=='new_signal'):
        intent='CONDITIONAL_SETUP'
    else: intent='ANALYSIS'
    if intent=='DIRECT_SIGNAL' and not (entry or sig.entry_type=='market'):
        intent='CONDITIONAL_SETUP'
    flags=[]
    if not source_ok: flags.append('SOURCE_INCOMPLETE')
    if not image_loaded: flags.append('CHART_IMAGE_UNAVAILABLE')
    if not entry: flags.append('NO_EXPLICIT_ENTRY')
    if not sl: flags.append('NO_SL')
    if not tp: flags.append('NO_TP')
    if trigger: flags.append('ENTRY_TRIGGER')
    if strong_call and trigger: flags.append('REVIEW_REQUIRED')
    # Entry price is not an order type. Require explicit language for MARKET/LIMIT.
    market_cue=bool(MARKET_CUE.search(src))
    limit_cue=bool(LIMIT_CUE.search(src))
    waiting=bool(WAIT_CUE.search(src)) or trigger
    if market_cue and not limit_cue and not waiting:
        entry_mode='market'
    elif limit_cue and not market_cue and not waiting:
        entry_mode='limit'
    elif waiting and not market_cue and not limit_cue:
        entry_mode='trigger'
    else:
        entry_mode='unknown'
    if entry and entry_mode=='unknown':
        flags.append('ENTRY_MODE_REVIEW_REQUIRED')
    if (market_cue and limit_cue) or (market_cue and waiting):
        flags.append('REVIEW_REQUIRED')
    extra={}
    r=RETEST.search(src)
    if r: extra['potential_retest_level']=r.group(1)
    t=TARGET.search(src)
    if t: extra['narrative_target']=t.group(1)
    if intent not in ('DIRECT_SIGNAL','CONDITIONAL_SETUP'):
        execution='NOT_A_TRADE'
    elif any(flag in flags for flag in ('SOURCE_INCOMPLETE','NO_EXPLICIT_ENTRY','NO_SL','NO_TP')):
        execution='SKIP_INCOMPLETE'
    elif 'REVIEW_REQUIRED' in flags or entry_mode=='unknown':
        execution='SKIP_ENTRY_MODE_UNCONFIRMED'
    elif entry_mode=='trigger' or intent=='CONDITIONAL_SETUP':
        execution='SKIP_TRIGGER_NOT_VERIFIED'
    else:
        execution='HISTORICAL_ONLY'
    return dict(intent=intent,side=side,execution=execution,flags=flags,extra=extra,
        model_reason=sig.reason,entry_mode=entry_mode,model_entry_mode=sig.entry_type)

async def review(p, url):
    idea, picture = await p.tv.fetch(url, with_image=True)
    if not idea.title and not idea.description:
        raise ValueError('TradingView source text unavailable: review not saved')
    original = (idea.title + '\n' + idea.description).strip()
    msg = TgMessage(channel_id=0,message_id=0,date=idea.published or datetime.now(timezone.utc), text=original, links=[url])
    channel = Channel(channel_id=0,title='TradingView historical re-review',username=None,enabled=False)
    sig = await p.ai.parse(channel,msg,[],[],[idea],[picture] if picture else [],datetime.now(timezone.utc))
    facets = classify(sig, original, image_loaded=bool(picture))
    return {'url':url,'published_at':idea.published.isoformat() if idea.published else None,
            'source_excerpt':original[:2000], 'source_length':len(original),
            'image_loaded':bool(picture),'classification':facets,'ai':sig.model_dump(mode='json'),
            'checked_at':datetime.now(timezone.utc).isoformat()}
