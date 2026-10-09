"""Focused, opt-in chart level verification. Independent from performance calculations."""
import json
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from .ai import image_type
from .tv_reconcile import candidate_rows, entry_touch

class EntryEvidence(BaseModel):
    entry: float | None = None
    stop_loss: float | None = None
    targets: list[float] = Field(default_factory=list)
    entry_source: str = Field(description="VISIBLE_LABEL, AUTHOR_TEXT, NONE")
    entry_label_or_quote: str = ""
    is_new_call: bool = False
    conditional: bool = False
    update_or_result: bool = False
    direction: str = Field(description="LONG, SHORT, UNCERTAIN")
    confidence: str = Field(description="HIGH, MEDIUM, LOW")
    explanation: str = ""

PROMPT = """Audit the single public TradingView idea at its ORIGINAL publication time. You receive its actual chart image and entire post text. Goal: identify only EXPLICIT numeric entry prices for a potential NEW trade. Do NOT estimate a position tool's entry from pixels, candle prices or the author's current/market price. Chart Entry must have an actually readable numeric label unequivocally attached to entry; quote that label in entry_label_or_quote. Text Entry must be a numeric level explicitly stated as an ENTRY (not an indicator, support, invalidation, price target or generic trading zone); quote the wording. An unspecific 'LONG here' / 'SHORT here' is NOT an explicit entry: set entry null. If visually ambiguous, set entry null and confidence LOW. Never invent SL/TP, labels, fills or results. Separate conditional setups, earlier signal updates, targets, and non-trade analysis. On update_or_result, is_new_call must be false. No implied filled orders. Output ONLY structured schema."""

def ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS tv_focused_entry_audit (
        idea_id TEXT PRIMARY KEY, evidence_json TEXT NOT NULL, checked_at TEXT NOT NULL)""")
    db.commit()

def read(db, iid):
    ensure(db)
    row=db.execute('SELECT evidence_json FROM tv_focused_entry_audit WHERE idea_id=?',(iid,)).fetchone()
    return json.loads(row[0]) if row else None

def save(db, iid, ev):
    ensure(db)
    db.execute('INSERT OR REPLACE INTO tv_focused_entry_audit VALUES (?,?,?)',
      (iid, ev.model_dump_json(), datetime.now(timezone.utc).isoformat()))
    db.commit()

def pending(db,handle,limit=15):
    return [r for r in candidate_rows(db,handle) if r['entry'] is None and not read(db,r['idea_id'])][:limit]

async def analyze(p, rec):
    if not hasattr(p.ai,'_generate'):
        raise RuntimeError('Gemini required for focused chart audit')
    idea, image=await p.tv.fetch(rec['url'],with_image=True)
    if not image: raise ValueError('TradingView chart image missing')
    from google.genai import types
    parts=[types.Part.from_bytes(data=image,mime_type=image_type(image)),
      f"URL: {rec['url']}\nOriginal publication: {rec['published_at']}\nSYMBOL: {rec['symbol']}\nTITLE: {idea.title}\nFULL TEXT:\n{idea.description[:12000]}"]
    return await p.ai._generate(p.ai.parse_model,PROMPT,parts,EntryEvidence,1800,90.0)

async def report(prices,db,handle):
    rows=[r for r in candidate_rows(db,handle) if r['entry'] is None]
    checked=[(r,read(db,r['idea_id'])) for r in rows]
    done=[(r,e) for r,e in checked if e is not None]
    lines=[f'🔍 @{handle}: точкова перевірка Entry {len(done)}/{len(rows)}',
      'Gemini тільки для ідей без Entry. Значення з графіка без читабельних чисел НЕ відновлюються.',
      'Окремий аудит; WIN/LOSS не змінюється, ордерів немає.']
    for r,e in done:
        entry=e.get('entry'); kind=e.get('entry_source') or 'NONE'
        note='NO_EXPLICIT_ENTRY'
        if entry is not None and e.get('is_new_call') and not e.get('update_or_result'):
            note=await entry_touch(prices,{**r,'entry':entry})
        elif entry is not None:
            note='NOT_NEW_TRADE_OR_UNCONFIRMED'
        lines.append(f"• {r['idea_id']} {r['symbol'] or '?'} {e.get('direction','?')} · Entry {entry if entry is not None else '—'} ({kind})\n"
          f"  {note} · new={e.get('is_new_call')} conditional={e.get('conditional')} update={e.get('update_or_result')}\n"
          f"  Доказ: {(e.get('entry_label_or_quote') or '—')[:130]} · confidence={e.get('confidence')}\n"
          f"  /tventryidea {r['idea_id']}")
    return '\n'.join(lines)
