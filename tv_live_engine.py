"""Opt-in TradingView -> BingX execution. Two independently protected Hedge entry orders.

Exchange acknowledgement is NOT a fill; every attempt is persisted before sending.
Never re-submit an uncertain request: operator reconciliation is required.
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from signalbot.bingx import BingX
from signalbot.models import ParsedSignal
from signalbot.risk import build_plan, PlanError
from tv_trade_diagnostics import bingx_pair, evaluate, targets_80_20
from tv_timeframe_policy import freshness, untouched_since_publication


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tv_live_orders (
        idea_id TEXT PRIMARY KEY, author TEXT, created_utc TEXT, updated_utc TEXT,
        status TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL)''')
    db.commit()


def record(db, iid, author, status, reason, payload=None):
    now = datetime.now(timezone.utc).isoformat()
    db.execute('''INSERT INTO tv_live_orders VALUES(?,?,?,?,?,?,?)
     ON CONFLICT(idea_id) DO UPDATE SET updated_utc=excluded.updated_utc,
      status=excluded.status, reason=excluded.reason, payload=excluded.payload''',
      (iid, author, now, now, status, reason, json.dumps(payload or {}, ensure_ascii=False, default=str)))
    db.commit()


def split_quantity(quantity, precision, minimum):
    unit = Decimal(1).scaleb(-precision)
    total = Decimal(str(quantity))
    first = (total * Decimal('0.8')).quantize(unit, rounding=ROUND_DOWN)
    second = total - first
    if first <= 0 or second <= 0 or min(first, second) < Decimal(str(minimum)):
        raise ValueError('Розмір позиції замалий для двох окремих виходів 80/20')
    return float(first), float(second)


def fresh(published, max_age_minutes=20):
    if not published: return False
    if published.tzinfo is None: published = published.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - published).total_seconds()
    return 0 <= seconds <= max_age_minutes*60


def signal_from_evidence(ev):
    return ParsedSignal(kind='new_signal', confidence=1, base_asset='BTC', quote_asset='USDT',
        market='futures', side=ev.direction.lower(), entry_type='limit',
        entry_low=float(ev.entry), entry_high=float(ev.entry), stop_loss=float(ev.stop_loss),
        take_profits=[float(t) for t in ev.targets], leverage=ev.leverage,
        margin_type=ev.margin_type, update_action='none', update_stop_loss=None,
        refers_to_message_id=None, numbers_from_image=True, stale_hints=[], reason='TradingView chart review')


async def execute(db, iid, author, ev, published, cfg):
    """Attempt at most once per idea. Uncertain calls NEVER get retried automatically."""
    schema(db)
    old = db.execute('SELECT status,reason FROM tv_live_orders WHERE idea_id=?', (iid,)).fetchone()
    if old:
        return old[0], 'Попередня спроба: '+old[1]
    import tv_watch_policy
    tv_watch_policy.initialize(db)
    if db.execute('SELECT 1 FROM tv_closed_ideas WHERE idea_id=?', (iid,)).fetchone():
        return 'SKIP', 'Автор уже закрив цей сигнал'
    allowed, age_reason = freshness(published, getattr(ev, 'timeframe', None), unknown_minutes=cfg.validator.max_age_minutes)
    if not allowed:
        return 'SKIP', age_reason
    pair = bingx_pair(ev.symbol)
    if not pair:
        return 'SKIP', 'Немає відповідного USDT-ф’ючерсу BingX'
    if cfg.trading_mode != 'live' or os.getenv('TV_LIVE_EXECUTION') != 'YES':
        return 'SHADOW', 'Торгівля TradingView не увімкнена (TRADING_MODE=live, TV_LIVE_EXECUTION=YES)'
    if os.getenv('TV_LIVE_ACK') != 'I_ACCEPT_TWO_REAL_ORDERS':
        return 'SHADOW', 'Немає підтвердження двох реальних ордерів TV_LIVE_ACK'
    if not cfg.secrets.bingx_api_key or not cfg.secrets.bingx_secret_key:
        return 'SKIP', 'Немає BingX API ключів'
    exchange = BingX(cfg.secrets.bingx_api_key, cfg.secrets.bingx_secret_key)
    try:
        info = await exchange.market_info(pair, 'futures')
        if not info: return 'SKIP', 'Ф’ючерсний контракт BingX недоступний'
        verdict, reason = evaluate(ev, info.price, True)
        if verdict != 'READY_FOR_EXECUTION_CHECK': return verdict, reason
        if (datetime.now(timezone.utc) - published).total_seconds() > cfg.validator.max_age_minutes * 60:
            try:
                replay_ok, replay_reason = await untouched_since_publication(exchange, pair, ev, published)
            except Exception as exc:
                return 'SKIP', f'Не вдалося перевірити історичні свічки BingX: {type(exc).__name__}'
            if not replay_ok: return 'SKIP', replay_reason
        if not await exchange.hedge_mode(): return 'SKIP', 'Потрібен Hedge Mode на BingX'
        if await exchange.futures_positions(pair): return 'SKIP', 'Уже є позиція на цьому символі; не змішуємо з угодою бота'
        if await exchange.futures_open_orders(pair): return 'SKIP', 'Уже є активні ордери на цьому символі'
        equity = await exchange.equity('futures')
        sig = signal_from_evidence(ev)
        cid = 'tv'+hashlib.sha256(iid.encode()).hexdigest()[:24]
        plan = build_plan(sig, info, equity, cfg.risk, cfg.validator.entry_tolerance_pct, cid)
        targets = targets_80_20(ev)
        if not targets: return 'SKIP', 'Немає двох валідних Take Profit'
        tp1, tp2 = [round(float(t), info.price_precision) for t in targets]
        if not ((plan.stop_loss < plan.entry_price < tp1 < tp2) if plan.side=='long' else
                (plan.stop_loss > plan.entry_price > tp1 > tp2)):
            return 'SKIP', 'TP1/TP2 або SL некоректні після округлення'
        q1, q2 = split_quantity(plan.quantity, info.qty_precision, info.min_qty)
        if min(q1, q2) * plan.entry_price < info.min_notional:
            return 'SKIP', 'Частина 20% не досягає мінімального номіналу біржі'
        if await exchange.open_positions() >= cfg.risk.max_open_positions:
            return 'SKIP', 'Досягнута межа активних позицій з конфігурації'
        legs = [replace(plan, quantity=q1, take_profit=tp1, client_id=cid+'a'),
                replace(plan, quantity=q2, take_profit=tp2, client_id=cid+'b')]
        # PRE-SEND durable barrier. Even timeout/ambiguous response must never auto-retry.
        record(db,iid,author,'SUBMITTING','Підготовлено два ордери; потрібна звірка BingX',
               {'symbol':pair,'legs':[asdict(p) for p in legs]})
        results=[]
        for index, leg in enumerate(legs):
            try:
                reply = await exchange.open_futures(leg, sig.margin_type or cfg.risk.margin_type, configure=index==0)
                results.append({'client_id':leg.client_id,'exchange':reply})
                record(db,iid,author,'PENDING_VERIFY',f'Відправлено {len(results)}/2 ордерів; перевіряємо біржу',
                       {'symbol':pair,'legs':[asdict(p) for p in legs],'responses':results})
            except Exception as err:
                reason=f'Біржа відхилила/не підтвердила ордер {len(results)+1}/2: {type(err).__name__}: {str(err)[:120]}; ручна звірка обов’язкова'
                record(db,iid,author,'MANUAL_RECONCILIATION',reason,
                       {'symbol':pair,'legs':[asdict(p) for p in legs],'responses':results})
                return 'MANUAL_RECONCILIATION',reason
        statuses=[]
        for leg in legs:
            try:
                order=await exchange.futures_order(pair,client_order_id=leg.client_id)
                statuses.append({'client_id':leg.client_id,'status':order.get('status'),'order':order})
            except Exception as err:
                statuses.append({'client_id':leg.client_id,'verification_error':str(err)[:120]})
        record(db,iid,author,'PENDING_VERIFY','Два ордери прийняті; статус входу/SL/TP потребує контролю',
               {'symbol':pair,'legs':[asdict(p) for p in legs],'responses':results,'orders':statuses})
        return 'PENDING_VERIFY', 'Два ордери прийняті BingX; це ще НЕ гарантує виконання входу або SL/TP'
    finally:
        await exchange.close()
