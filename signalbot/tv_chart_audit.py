"""Explicit image+text evidence audit. Never auto-promote to traded signals."""
import json
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from .ai import image_type

class ChartEvidence(BaseModel):
    source_has_image: bool = False
    position_tool: str = Field(description="LONG, SHORT, NONE or UNCERTAIN")
    chart_entry: float | None = None
    chart_stop_loss: float | None = None
    chart_targets: list[float] = Field(default_factory=list)
    text_entry: float | None = None
    text_stop_loss: float | None = None
    text_targets: list[float] = Field(default_factory=list)
    setup: str = Field(description="DIRECT, CONDITIONAL, COMMENTARY, UPDATE, RESULT or UNCERTAIN")
    entry_condition: str = ""
    author_claims_entry_now: bool = False
    previous_idea_reference: bool = False
    chart_evidence: str = ""
    text_evidence: str = ""
    uncertain: list[str] = Field(default_factory=list)
    verdict: str = Field(description="CANDIDATE, REVIEW_REQUIRED or NO_TRADE")

SYSTEM = '''You audit PUBLIC TradingView ideas historically. Carefully examine BOTH the attached chart screenshot and the entire original text. Never assume a colored rectangle is a real executed trade; price projection, position tool and recorded order are different. Only extract numeric values if clearly visible; never infer from pixels when prices are not labeled. Distinguish what is visible on the image from what is expressly written in the text. If chart is illegible, set position_tool UNCERTAIN, chart levels null and explain uncertainty. A forecast, future promise to post a signal, or update of a prior signal must not be counted as a new trade. Explicitly separate entry conditions (breakout/retest/limit) and new immediate calls. Do not calculate profit, infer trade execution, or invent missing TP/SL. Give concise evidence in chart_evidence/text_evidence. verdict CANDIDATE only if evidence supports a potential new trade, REVIEW_REQUIRED for ambiguous or incomplete, NO_TRADE for commentary/results. Output schema only.'''

def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tv_chart_audit (
      idea_id TEXT PRIMARY KEY, evidence_json TEXT NOT NULL, checked_at TEXT NOT NULL)''')
    db.commit()

def pending(db, handle, limit):
    ensure(db)
    return db.execute('''SELECT i.idea_id, i.url FROM tv_ideas i
      LEFT JOIN tv_chart_audit a ON a.idea_id=i.idea_id
      WHERE i.handle=? AND a.idea_id IS NULL
      ORDER BY i.added_at DESC,i.idea_id LIMIT ?''',(handle,limit)).fetchall()

def save(db, iid, evidence):
    ensure(db)
    db.execute('INSERT OR REPLACE INTO tv_chart_audit VALUES (?,?,?)',
       (iid, evidence.model_dump_json(), datetime.now(timezone.utc).isoformat()))
    db.commit()

def read(db, iid):
    ensure(db)
    r=db.execute('SELECT evidence_json FROM tv_chart_audit WHERE idea_id=?',(iid,)).fetchone()
    return json.loads(r[0]) if r else None

def summary(db,handle):
    ensure(db)
    rows=db.execute('''SELECT a.evidence_json FROM tv_chart_audit a
      JOIN tv_ideas i ON i.idea_id=a.idea_id WHERE i.handle=?''',(handle,)).fetchall()
    total=db.execute('SELECT count(*) FROM tv_ideas WHERE handle=?',(handle,)).fetchone()[0]
    counts={}
    for r in rows:
        try: k=json.loads(r[0]).get('verdict','REVIEW_REQUIRED')
        except (ValueError,TypeError): k='REVIEW_REQUIRED'
        counts[k]=counts.get(k,0)+1
    return total,len(rows),counts

async def analyze(p, url):
    if not hasattr(p.ai, '_generate'):
        raise RuntimeError('Chart-first audit currently requires Gemini provider')
    idea, picture = await p.tv.fetch(url, with_image=True)
    if not picture:
        raise ValueError('Chart image not available; do not save a visual verdict')
    if not (idea.title or idea.description):
        raise ValueError('TradingView idea text missing')
    from google.genai import types
    parts=[types.Part.from_bytes(data=picture,mime_type=image_type(picture)),
      f'URL: {url}\nPublished: {idea.published}\nChart symbol: {idea.symbol}\nTITLE: {idea.title}\nFULL DESCRIPTION:\n{idea.description[:12000]}']
    result=await p.ai._generate(p.ai.parse_model,SYSTEM,parts,ChartEvidence,2200,90.0)
    result.source_has_image=True
    return result
