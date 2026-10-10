"""TradingView Gemini review; LIVE is an independent, explicitly gated opt-in."""
import asyncio
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from pydantic import BaseModel, Field

from signalbot.ai import GeminiAI, image_type
from signalbot.config import load_config
from signalbot.tradingview import TradingView
import tv_last_hour
import tv_author_settings as settings
import tv_auto_alerts as alerts
from tv_trade_diagnostics import age_label, evaluate, bingx_pair, targets_80_20
from tv_live_engine import execute as execute_live


class Evidence(BaseModel):
    category: str = Field(description='NEW_CALL, CONDITIONAL, UPDATE_RESULT, ANALYSIS, UNCERTAIN')
    symbol: str = ''
    direction: str = Field(description='LONG, SHORT, UNKNOWN')
    timeframe: str | None = Field(default=None, description='Explicit idea timeframe (1m, 5m, 15m, 1h, 4h, 1d, 1w); null if unclear')
    entry: float | None = None
    stop_loss: float | None = None
    targets: list[float] = Field(default_factory=list)
    leverage: int | None = None
    margin_type: str | None = Field(default=None, description='ISOLATED, CROSSED or null')
    entry_evidence: str = ''
    stop_evidence: str = ''
    targets_evidence: str = ''
    confidence: str = Field(description='HIGH, MEDIUM, LOW')
    details: str = ''


PROMPT = """Review one public TradingView idea at its ORIGINAL publication time. Read the full text and provided chart image (if available). Extract only explicitly written or legibly labelled numbers: entry, stop, take-profits, leverage, margin mode, and the explicitly stated chart/idea timeframe (null if unknown). Do NOT estimate levels using candle locations, chart grid, unlabelled position tool zones, axis ticks, or current market price. If unreadable, return null and explain. Distinguish an actionable new call from an analysis, a conditional setup and a historical result. Never turn BUY SETUP alone into an instruction to trade now. If chart image is unavailable or mismatches title/symbol, state uncertainty. Do not decide to trade, do not infer order fills. Return structured JSON only."""


def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS tv_full_chart_reviews
                  (idea_id TEXT PRIMARY KEY, author TEXT, reviewed_utc TEXT NOT NULL,
                   result_json TEXT NOT NULL, had_chart INTEGER NOT NULL)""")
    db.commit()


def format_result(author, iid, url, evidence, has_chart, published=None, decision=None, live_price=None):
    status = evidence.category + ' / ' + evidence.confidence
    lines = ['🧠 <b>Аналіз TradingView</b> @' + escape(author),
             '<b>' + escape(evidence.symbol or iid) + '</b> — ' + escape(status),
             'Напрямок: ' + escape(evidence.direction),
             'ENTRY: ' + escape(str(evidence.entry)) if evidence.entry is not None else 'ENTRY: —',
             'SL: ' + escape(str(evidence.stop_loss)) if evidence.stop_loss is not None else 'SL: —',
             'TP: ' + escape(', '.join(map(str, evidence.targets))) if evidence.targets else 'TP: —',
             'Плече: ' + escape(str(evidence.leverage)) if evidence.leverage else 'Плече: —',
             'Маржа: ' + escape(evidence.margin_type or '—'),
             'Графік: ' + ('отримано' if has_chart else 'недоступний'),
             'Таймфрейм: ' + escape(evidence.timeframe or 'невідомий'),
             'Опубліковано: ' + escape(age_label(published)),
             escape(evidence.details[:450]),
             '<a href="' + escape(url,quote=True) + '">Відкрити ідею</a>',
             '⚠️ Угоду не відкрито: ' + escape((decision or ('CHECK', 'AI-аналіз не підключено до BingX-виконавця'))[1]),
             'Статус: ' + escape((decision or ('CHECK', ''))[0]) + (' | BingX ' + escape(str(live_price)) if live_price is not None else '')] 
    return '\n'.join(lines)


async def run(hours=1, max_ideas=6, notify=False, force=False, data_dir='/data', send_now=True):
    if max_ideas < 1:
        raise ValueError('max-ideas must be positive')
    cfg = load_config('config.toml')
    if cfg.ai.provider != 'gemini' or not cfg.secrets.gemini_api_key:
        raise RuntimeError('GEMINI_API_KEY is required; no review performed')
    data = Path(data_dir)
    with sqlite3.connect(str(data/'signalbot.db')) as db:
        authors=settings.enabled_authors(db)
    eventdb=sqlite3.connect(str(data/'tv_updates_monitor.sqlite'),timeout=20)
    init(eventdb); alerts.initialize(eventdb)
    candidates=[]
    cutoff=datetime.now(timezone.utc).timestamp()-hours*3600
    try:
        for author in authors:
            if settings.suspension_info(eventdb, author):
                continue
            try:
                entries=alerts.fetch_latest(author_name=author)
                for entry in entries:
                    record=alerts.unpack(entry, author_name=author)
                    published=tv_last_hour.published_at(entry)
                    if record and published and cutoff <= published.timestamp() <= datetime.now(timezone.utc).timestamp()+300:
                        iid,url,title=record
                        if not force and eventdb.execute('SELECT 1 FROM tv_full_chart_reviews WHERE idea_id=?',(iid,)).fetchone():
                            continue
                        candidates.append((published, author, iid, url, title, entry))
            except Exception as exc:
                print(f'@{author} FETCH_FAILED: {type(exc).__name__}: {exc}')
        candidates.sort(reverse=True)
        candidates=candidates[:max_ideas]
        print(f'Authors={len(authors)} New uncached ideas to analyze={len(candidates)} (max={max_ideas})')
        if not candidates: return
        tv=TradingView(timeout=20.0)
        ai=GeminiAI(cfg.secrets.gemini_api_key,cfg.ai.parse_model,cfg.ai.onboard_model,
                    cfg.ai.fallback_model,cfg.ai.requests_per_minute)
        try:
            from google.genai import types
            for _,author,iid,url,title,entry in candidates:
                try:
                    idea,img=await tv.fetch(url,with_image=True)
                    desc=idea.description or str(entry.get('description') or '')
                    parts=[f'Author: {author}\nURL: {url}\nTitle: {title}\nSymbol: {idea.symbol}\nPublished: {tv_last_hour.published_at(entry)}\nFULL TEXT:\n{desc[:14000]}']
                    if img:
                        parts.insert(0,types.Part.from_bytes(data=img,mime_type=image_type(img)))
                    ev=await ai._generate(ai.parse_model,PROMPT,parts,Evidence,1200,90.0,pauses=(0.0,))
                    if ev.margin_type not in (None,'ISOLATED','CROSSED'):
                        ev.margin_type=None
                    with eventdb:
                        eventdb.execute('INSERT OR REPLACE INTO tv_full_chart_reviews VALUES (?,?,?,?,?)',
                            (iid,author,datetime.now(timezone.utc).isoformat(),ev.model_dump_json(),int(bool(img))))
                    # Read-only exchange status. No API keys and no trading requests.
                    pair = bingx_pair(ev.symbol)
                    listed = None
                    live_price = None
                    if pair and ev.category.upper() in ('NEW_CALL', 'CONDITIONAL') and ev.entry is not None:
                        try:
                            from signalbot.bingx import BingX
                            exchange = BingX('', '')
                            try:
                                info = await exchange.market_info(pair, 'futures')
                                listed = info is not None
                                live_price = info.price if info else None
                            finally:
                                await exchange.close()
                        except Exception as check_err:
                            print(f'@{author} {iid}: BINGX_READ_FAILED {type(check_err).__name__}: {str(check_err)[:100]}')
                    decision = evaluate(ev, live_price, listed)
                    # Auto execution is separately opt-in. Never replay cached or stale ideas.
                    if cfg.trading_mode == 'live' and os.getenv('TV_LIVE_EXECUTION') == 'YES':
                        try:
                            trade_status, trade_reason = await execute_live(eventdb, iid, author, ev, tv_last_hour.published_at(entry), cfg)
                            decision = (trade_status, trade_reason)
                        except Exception as trade_error:
                            decision = ('EXECUTION_ERROR', f'{type(trade_error).__name__}: {str(trade_error)[:160]}')
                    tp_split = targets_80_20(ev)
                    print(f'@{author} {iid}: {ev.category}/{ev.confidence} '
                          f'age={age_label(tv_last_hour.published_at(entry))} '
                          f'entry={ev.entry} sl={ev.stop_loss} tp={ev.targets} '
                          f'chart={bool(img)} bingx={live_price} status={decision[0]} reason={decision[1]}'
                          + (f' TP80/20_PROPOSED={tp_split}' if tp_split else ''))
                    if notify:
                        alerts.enqueue(eventdb,'fullreview:'+iid,format_result(author,iid,url,ev,bool(img),tv_last_hour.published_at(entry),decision,live_price))
                except Exception as exc:
                    print(f'@{author} {iid}: REVIEW_FAILED ({type(exc).__name__}: {str(exc)[:160]})')
        finally:
            await tv.close()
        if notify and send_now: alerts.deliver(eventdb)
    finally:
        eventdb.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--hours',type=float,default=1)
    parser.add_argument('--max-ideas',type=int,default=6)
    parser.add_argument('--notify',action='store_true')
    parser.add_argument('--force',action='store_true',help='Repeat cached Gemini reviews (additional API cost)')
    args=parser.parse_args()
    asyncio.run(run(args.hours,args.max_ideas,args.notify,args.force,os.getenv('SIGNALBOT_DATA_DIR','/data')))
