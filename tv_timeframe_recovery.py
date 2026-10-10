"""Recover missing timeframe in legacy cached reviews without altering trade levels.

Explicit text/metadata first; Gemini chart-only fallback, once per idea. A failed
recovery is stored as unknown and never prompts a guessed timeframe.
"""
import json
import re
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from tv_timeframe_policy import normalize_timeframe
from signalbot.tradingview import TradingView


class TimeframeAnswer(BaseModel):
    timeframe: str | None = Field(default=None, description='Only explicit original chart timeframe or null')
    evidence: str = ''


_RE = re.compile(r'(?i)\b(?:time\s*frame|timeframe|chart\s*interval|chart\s*tf|tf)\s*[:=\-]\s*(1\s*(?:m|h|d|w|min|hour|day|week)|3m|5m|15m|30m|2h|4h|6h|8h|12h|3d)\b')


def explicit_timeframe(text):
    found = {normalize_timeframe(m.group(1).replace(' ', '')) for m in _RE.finditer(text or '')}
    found.discard(None)
    return next(iter(found)) if len(found) == 1 else None


def metadata_timeframe(entry):
    # Trusted structured fields only: don't mistake an API timestamp for interval.
    data = entry.get('data', entry) if isinstance(entry, dict) else {}
    if not isinstance(data, dict):
        return None
    for key in ('timeframe', 'interval', 'resolution', 'chart_interval'):
        value = data.get(key)
        if value is not None:
            tf = normalize_timeframe(value)
            if tf:
                return tf
    return None


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tv_timeframe_recovery (
        idea_id TEXT PRIMARY KEY, checked_utc TEXT NOT NULL,
        timeframe TEXT, source TEXT NOT NULL, reason TEXT NOT NULL)''')
    db.commit()


async def recover(db, iid, entry, url, cached, cfg):
    """Return (updated Evidence, source); only change timeframe if evidenced."""
    init(db)
    if normalize_timeframe(cached.timeframe):
        return cached, 'cached'
    previous = db.execute('SELECT timeframe,source FROM tv_timeframe_recovery WHERE idea_id=?', (iid,)).fetchone()
    if previous:
        tf = normalize_timeframe(previous[0])
        if tf:
            cached.timeframe = tf
        return cached, previous[1]
    tf = metadata_timeframe(entry)
    source = 'tradingview_metadata' if tf else 'unresolved'
    idea = None
    img = None
    tv = None
    try:
        if not tf:
            tv = TradingView(timeout=20)
            idea, img = await tv.fetch(url, with_image=True)
            if idea.status != 200:
                source = f'page_unavailable_{idea.status}'
            else:
                tf = explicit_timeframe(idea.description or '')
                if tf:
                    source = 'explicit_idea_text'
        # Chart may have explicitly labelled TF. Gemini must not estimate from candles.
        if not tf and img and cfg.secrets.gemini_api_key:
            from google.genai import types
            from signalbot.ai import GeminiAI, image_type
            ai = GeminiAI(cfg.secrets.gemini_api_key, cfg.ai.parse_model,
                          cfg.ai.onboard_model, cfg.ai.fallback_model,
                          cfg.ai.requests_per_minute)
            prompt = ('Read ONLY the timeframe explicitly written on this ORIGINAL TradingView chart, '
                      'or explicitly stated in the supplied idea text. Return null if absent, '
                      'ambiguous or illegible. Do not infer timeframe from price movements, '
                      'candle count, publication age or strategy. Do not change trading levels.')
            parts = [types.Part.from_bytes(data=img, mime_type=image_type(img)),
                     f'IDEA TEXT:\n{(idea.description or "")[:10000]}']
            answer = await ai._generate(ai.parse_model, prompt, parts, TimeframeAnswer,
                                        300, 60.0, pauses=(0.0,))
            tf = normalize_timeframe(answer.timeframe)
            if tf:
                source = 'gemini_explicit_chart'
    except Exception as exc:
        source = 'recovery_error_' + type(exc).__name__
    finally:
        if tv:
            await tv.close()
    # Temporary network/AI errors should be retried next time, not cached forever.
    if source.startswith('page_unavailable_') or source.startswith('recovery_error_'):
        return cached, source
    with db:
        db.execute('INSERT OR IGNORE INTO tv_timeframe_recovery VALUES (?,?,?,?,?)',
                   (iid, datetime.now(timezone.utc).isoformat(), tf, source,
                    'Only explicit TF accepted; never assume from age'))
        if tf:
            cached.timeframe = tf
            # Preserve all other existing AI levels; update only the TF field.
            row = db.execute('SELECT result_json FROM tv_full_chart_reviews WHERE idea_id=?', (iid,)).fetchone()
            if row:
                raw = json.loads(row[0]); raw['timeframe'] = tf
                db.execute('UPDATE tv_full_chart_reviews SET result_json=? WHERE idea_id=?',
                           (json.dumps(raw, ensure_ascii=False), iid))
    return cached, source
