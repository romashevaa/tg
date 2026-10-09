from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import timedelta
from collections import defaultdict
from dataclasses import asdict, replace

from .config import Config
from .evaluate import Candle, Trade, drift, levels_ok, simulate, structure, summarize, summarize_drift
from .executor import Executor
from .models import Channel, Decision, OrderPlan, ParsedSignal, TgMessage, TvIdea
from .numbers import numbers_in_text, ungrounded
from .storage import Storage, now
from .models import HistSignal
from .textparse import FOREX_RE, RESULT_RE, bare_call, URL_RE, find_coin, find_side, looks_like_call, parse_call, find_margin_type
from .telegram import message_link
from .tradingview import find_tv_links
from .validator import decide, market_for, symbol_for

log = logging.getLogger("signalbot")

PAPER_EQUITY = 1000.0
ICON = {"execute": "✅", "manual": "🟡", "reject": "⛔", "ignore": "·"}
Actions = list[tuple[str, str]]
RULES_REASON = "template call read by rules, no model used"
TEASER_RE = re.compile(r"🔐|(?i:vip\s*only)")
MAX_IMAGE_CAPTION = 1200
SHORT_CAPTION = 200
# A pitch for the paid group under a picture: the picture is a boast, not a call.
PROMO_RE = re.compile(r"(?i)\bjoin\b|subscri|book your|vip family|membership|discount|\bsale\b|message\s*:|"
                      r"підпис|приєдн|вступа|подпис|присоедин|вступи|скидк|знижк")
# In automatic mode only posts that name a coin, have no caption or got target reports are read.
AUTO_IMAGE_RANK = 2



def call_from_idea(idea: TvIdea, message_id: int):
    """A template call written inside a TradingView idea: returns (call, source text) or None."""
    for text in idea.call_texts():
        call = parse_call(text, message_id)
        if call is not None:
            return call, text
    return None


def call_to_parsed(call, text: str) -> ParsedSignal:
    """A call read by rules, in the same shape the model returns."""
    futures = call.leverage is not None or re.search(r"(?i)futures|leverage|плеч|фьючерс|ф'ючерс", text)
    return ParsedSignal(
        kind="new_signal", confidence=1.0, base_asset=call.base_asset, quote_asset=call.quote_asset,
        market="futures" if futures else "unknown", side=call.side,
        entry_type="limit" if call.entries else "market",
        entry_low=min(call.entries) if call.entries else None,
        entry_high=max(call.entries) if call.entries else None,
        stop_loss=call.stop_loss, take_profits=call.take_profits, leverage=call.leverage,
        margin_type=find_margin_type(text),
        update_action="none", update_stop_loss=None, refers_to_message_id=None,
        numbers_from_image=False, stale_hints=[], reason=RULES_REASON,
    )


def needs_image(msg: TgMessage, channel: Channel) -> bool:
    """Send the photo to the model unless the text already holds the whole call.
    A post often names the coin and the entry in words and leaves the stop and the targets
    drawn on the chart, so a few numbers in the text do not make the picture redundant."""
    if not msg.has_photo:
        return False
    if channel.profile and channel.profile.levels_in_images:
        return True
    # Always inspect an attached chart for multimodal consistency, including
    # when the caption contains a complete template or reports previous profits.
    return True


def image_rank(msg: TgMessage, has_results: bool) -> int:
    """How likely a photo post is a call whose levels sit on the picture.
    The strongest sign is the channel itself reporting targets in replies to the post. Next comes
    a short caption: long texts under a chart are market commentary far more often than calls."""
    text = URL_RE.sub(" ", msg.text).strip()
    coin = find_coin(text)
    rank = 4 if has_results else 0
    if len(text) < SHORT_CAPTION:
        rank += 2 if coin is not None or not text else 1
    if coin is not None and find_side(text) is not None:
        rank += 1
    if PROMO_RE.search(msg.text):
        rank -= 2
    return rank


def pick_image_posts(posts: list[TgMessage], limit: int, with_results: set[int] = frozenset(),
                     min_rank: int = 0) -> list[TgMessage]:
    """The most promising photo posts within the request budget, back in chronological order."""
    ranked = [(image_rank(m, m.message_id in with_results), m) for m in posts]
    best = sorted((x for x in ranked if x[0] >= min_rank), key=lambda x: (x[0], x[1].message_id), reverse=True)
    return sorted((m for _, m in best[:limit]), key=lambda m: m.message_id)


def worth_parsing(msg: TgMessage, has_open_signals: bool) -> bool:
    """Cheap filter before the model: a call needs numbers, a picture or a chart link.
    Text without digits can only matter as an update ("close BTC") to an open position."""
    if msg.has_photo or find_tv_links(msg.links):
        return True
    if not msg.text.strip():
        return False
    if re.search(r"\d", msg.text):
        return True
    return has_open_signals


class Pipeline:
    def __init__(self, cfg: Config, storage: Storage, ai, exchange, executor: Executor, tv=None, tg=None,
                 notifier=None, prices=None):
        self.cfg = cfg
        self.storage = storage
        self.ai = ai
        self.exchange = exchange
        self.executor = executor
        self.tv = tv
        self.tg = tg
        self.notifier = notifier
        self.prices = prices
        self.watched: set[int] = set()
        self.onboard_error = ""
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.refresh_watched()

    def refresh_watched(self) -> None:
        self.watched = {c.channel_id for c in self.storage.channels(only_enabled=True)}

    def is_watched(self, channel_id: int) -> bool:
        return channel_id in self.watched

    def link(self, channel: Channel, message_id: int) -> str:
        return message_link(channel.channel_id, channel.username, message_id)

    # live messages

    async def handle(self, msg: TgMessage, edited: bool = False) -> Decision | None:
        async with self._locks[msg.channel_id]:
            try:
                return await self._handle(msg, edited)
            except Exception as e:
                log.exception("Failed on %s", msg.key)
                await self._notify(f"⚠️ Помилка обробки {msg.key}: {e}")
                return None

    async def _handle(self, msg: TgMessage, edited: bool) -> Decision | None:
        started = time.monotonic()
        channel = self.storage.channel(msg.channel_id)
        if channel is None or not channel.enabled:
            return None
        # Channel posts are "edited" on every reaction or view counter change: skip those.
        if not self.storage.save_message(msg) and edited:
            return None
        open_rows = self.storage.open_signals(msg.channel_id)
        if not worth_parsing(msg, bool(open_rows)):
            return None
        if edited and self.storage.message_was_traded(msg.channel_id, msg.message_id):
            await self._notify(
                f"✏️ {channel.title}: повідомлення змінено після відкриття угоди\n"
                f"{self.link(channel, msg.message_id)}\n\n{msg.text[:400]}"
            )
            return None

        cfg = self.cfg.for_channel(channel.channel_id, channel.username)
        sig, ideas = await self._parse(cfg, channel, msg, open_rows)

        if sig.kind == "update":
            return await self._handle_update(channel, msg, sig, started)
        if sig.kind != "new_signal":
            self.storage.add_signal(msg, sig, None, None, "ignore", [sig.reason], None, "ignored",
                                    latency_ms=self._ms(started))
            if cfg.app.notify_ignored:
                await self._notify(f"· {channel.title}: {sig.kind} — {sig.reason}\n{self.link(channel, msg.message_id)}")
            return Decision("ignore", [sig.reason])

        decision, info = await self._decide(cfg, channel, msg, sig, ideas, force=False)
        return await self._act(cfg, channel, msg, sig, decision, info, started)

    async def _parse(self, cfg: Config, channel: Channel, msg: TgMessage, open_rows) -> tuple[ParsedSignal, list[TvIdea]]:
        # A fresh post in a fixed template needs no model: rules read it instantly and for free.
        # Replies and forwards always go to the model, which judges follow-ups and showcases.
        # A fast rules-only path is safe for *text-only* standalone template calls.
        # Never short-circuit a TradingView or photo post: the graphic and its
        # description may carry missing levels, entry conditions or evidence that
        # this is a retrospective result rather than a new executable signal.
        has_chart_link = bool(find_tv_links(msg.links))
        if not msg.reply_to and not msg.fwd_date and not msg.has_photo and not has_chart_link:
            call = parse_call(msg.text, msg.message_id)
            if call is not None:
                return call_to_parsed(call, msg.text), []
        ideas, images = await self._enrich(msg, channel)
        context = self.storage.recent_messages(msg.channel_id, msg.message_id, cfg.app.context_messages)
        open_lines = [
            f"message_id={r['message_id']} {r['symbol']} {r['side']} entry={r['entry']}" for r in open_rows
        ]
        sig: ParsedSignal = await self.ai.parse(channel, msg, context, open_lines, ideas, images, now())
        sig.confidence = max(0.0, min(1.0, sig.confidence))
        return sig, ideas

    async def _enrich(self, msg: TgMessage, channel: Channel) -> tuple[list[TvIdea], list[bytes]]:
        ideas: list[TvIdea] = []
        images: list[bytes] = []
        tasks = []
        if self.tv:
            tasks += [self.tv.fetch(u) for u in find_tv_links(msg.links)[:2]]
        photo_task = None
        if self.tg and needs_image(msg, channel):
            photo_task = self.tg.photo(msg.channel_id, msg.message_id)
        results = await asyncio.gather(*tasks, *([photo_task] if photo_task else []), return_exceptions=True)
        for res in results[: len(tasks)]:
            if isinstance(res, tuple):
                ideas.append(res[0])
                if res[1]:
                    images.append(res[1])
        if photo_task and isinstance(results[-1], (bytes, bytearray)):
            images.insert(0, bytes(results[-1]))
        return ideas, images

    async def _decide(self, cfg: Config, channel: Channel, msg: TgMessage, sig: ParsedSignal,
                      ideas: list[TvIdea], force: bool):
        symbol = symbol_for(sig)
        market = market_for(sig, channel.profile.default_market if channel.profile else None,
                            cfg.risk.default_market)
        info = await self.exchange.market_info(symbol, market) if symbol else None
        equity, positions = await self._account(market)
        source = "\n".join([msg.text] + [f"{i.title}\n{i.description}\n{i.page_excerpt}" for i in ideas])
        decision = decide(sig, msg, ideas, source, info, equity, positions, cfg, self.storage, now(), force)
        return decision, info

    async def _account(self, market: str) -> tuple[float, int]:
        if not self.executor.is_real(market):
            # Paper account: equity is nominal, positions are whatever was "opened" in the last day.
            return PAPER_EQUITY, len(self.storage.open_signals(days=1))
        return await self.exchange.equity(market), await self.exchange.open_positions()

    async def _act(self, cfg: Config, channel: Channel, msg: TgMessage, sig: ParsedSignal,
                   decision: Decision, info, started: float) -> Decision:
        plan = decision.plan
        status, response = {"execute": "pending", "manual": "awaiting", "reject": "rejected"}[decision.action], None
        if decision.action == "execute":
            try:
                status, response = await self.executor.open(plan, info, cfg, sig.margin_type)
            except Exception as e:
                status, response = "error", {"error": str(e)}
        entry = plan.entry_price if plan else (sig.entry_low or sig.entry_high)
        signal_id = self.storage.add_signal(
            msg, sig, symbol_for(sig), entry, decision.action, decision.reasons,
            asdict(plan) if plan else None, status, response, self._ms(started),
        )
        actions: Actions | None = None
        if decision.action == "manual":
            actions = [("✅ Відкрити", f"ok:{signal_id}"), ("❌ Відхилити", f"no:{signal_id}")]
        await self._notify(
            self._report(channel, msg, sig, decision, status, signal_id, response, started), actions
        )
        return decision

    async def _handle_update(self, channel: Channel, msg: TgMessage, sig: ParsedSignal, started: float) -> Decision:
        target = self._find_target(msg, sig)
        reasons = [f"оновлення: {sig.update_action}", sig.reason]
        status, response = "noted", None
        if target is not None and sig.update_action in ("close", "cancel"):
            plan = OrderPlan(**json.loads(target["plan"]))
            try:
                if sig.update_action == "close":
                    info = await self.exchange.market_info(plan.symbol, plan.market) if plan.market == "spot" else None
                    status, response = await self.executor.close(plan, info)
                else:
                    status, response = await self.executor.cancel(plan)
                self.storage.set_signal_status(target["id"], status, response)
            except Exception as e:
                status, response = "error", {"error": str(e)}
        self.storage.add_signal(msg, sig, target["symbol"] if target is not None else symbol_for(sig), None,
                                "update", reasons, None, status, response, self._ms(started))
        src = self.link(channel, msg.message_id)
        if target is None:
            text = f"ℹ️ {channel.title}: оновлення ({sig.update_action}) без відкритої угоди\n{sig.reason}\n{src}"
        elif status in ("closed", "cancelled"):
            text = f"🔻 {channel.title}: {target['symbol']} — {status} (сигнал #{target['id']})\n{src}"
        elif status == "error":
            text = (f"⚠️ {channel.title}: не вдалося виконати {sig.update_action} для #{target['id']}: "
                    f"{response['error']}\n{src}")
        else:
            sl = f", новий стоп {sig.update_stop_loss:g}" if sig.update_stop_loss else ""
            text = (f"🟡 {channel.title}: {target['symbol']} — {sig.update_action}{sl}. "
                    f"Потрібна ручна дія (сигнал #{target['id']})\n{src}\n\n{msg.text[:300]}")
        await self._notify(text)
        return Decision("ignore", reasons)

    def _find_target(self, msg: TgMessage, sig: ParsedSignal):
        rows = self.storage.open_signals(msg.channel_id)
        for ref in (sig.refers_to_message_id, msg.reply_to):
            for r in rows:
                if ref and r["message_id"] == ref and r["plan"]:
                    return r
        symbol = symbol_for(sig)
        same = [r for r in rows if r["symbol"] == symbol and r["plan"]]
        return same[-1] if same else None

    # owner actions

    async def decline(self, signal_id: int) -> str:
        row = self.storage.signal(signal_id)
        if row is None or row["status"] != "awaiting":
            return f"Сигнал #{signal_id} не очікує підтвердження"
        self.storage.set_signal_status(signal_id, "declined")
        return f"Сигнал #{signal_id} відхилено"

    async def approve(self, signal_id: int) -> str:
        row = self.storage.signal(signal_id)
        if row is None or row["status"] != "awaiting":
            return f"Сигнал #{signal_id} не очікує підтвердження"
        channel = self.storage.channel(row["channel_id"])
        cfg = self.cfg.for_channel(channel.channel_id, channel.username)
        sig = ParsedSignal.model_validate_json(row["parsed"])
        msg = self.storage.recent_messages(row["channel_id"], row["message_id"] + 1, 1)[-1]
        # Fresh price and fresh plan: hard checks still apply, soft doubts are overridden.
        self.storage.set_signal_status(signal_id, "approving")
        sig.numbers_from_image = True  # the owner has read the source; skip the text match
        try:
            decision, info = await self._decide(cfg, channel, msg, sig, [], force=True)
        except Exception as e:
            self.storage.set_signal_status(signal_id, "awaiting")
            return f"⚠️ #{signal_id}: {e}"
        if decision.action != "execute":
            self.storage.set_signal_status(signal_id, "rejected")
            return f"⛔ #{signal_id} вже не можна виконати: " + "; ".join(decision.reasons)
        try:
            status, response = await self.executor.open(decision.plan, info, cfg, sig.margin_type)
        except Exception as e:
            status, response = "error", {"error": str(e)}
        self.storage.db.execute(
            "UPDATE signals SET plan=?, entry=? WHERE id=?",
            (json.dumps(asdict(decision.plan)), decision.plan.entry_price, signal_id),
        )
        self.storage.set_signal_status(signal_id, status, response)
        tail = f"\n{response['error']}" if status == "error" else ""
        return f"✅ #{signal_id}: {self._plan_line(decision.plan)} [{status}]{tail}"

    async def onboard(self, ref: str) -> Channel:
        channel = await self.tg.resolve(ref)
        history = await self.tg.history(channel.channel_id, self.cfg.app.history_limit)
        for m in history:
            self.storage.save_message(m)
        usable = [m for m in history if m.text or m.has_photo]
        try:
            channel.profile = await self.ai.onboard(channel, usable)
            channel.enabled = channel.profile.tradable
        except Exception as e:
            # The model being unavailable must not block adding and evaluating a channel:
            # template calls are read by rules. Live following stays off until a profile exists.
            log.warning("No profile for %s: %s", channel.title, e)
            channel.profile, channel.enabled = None, False
            self.onboard_error = str(e)[:200]
        else:
            self.onboard_error = ""
        self.storage.upsert_channel(channel)
        self.refresh_watched()
        return channel

    def set_enabled(self, channel_id: int, enabled: bool) -> None:
        self.storage.set_enabled(channel_id, enabled)
        self.refresh_watched()

    async def check(self, channel_id: int, count: int) -> list[tuple[TgMessage, ParsedSignal | None, str]]:
        """Re-read the latest messages of a channel through the model without trading anything.
        Returns (message, reading, note) so the owner can compare the reading with the source."""
        channel = self.storage.channel(channel_id)
        cfg = self.cfg.for_channel(channel.channel_id, channel.username)
        if self.tg:
            for m in await self.tg.history(channel_id, count * 3):
                self.storage.save_message(m)
        recent = self.storage.recent_messages(channel_id, None, count * 3)
        picked = [m for m in recent if worth_parsing(m, False)][-count:]
        out: list[tuple[TgMessage, ParsedSignal | None, str]] = []
        for m in picked:
            try:
                sig, _ = await self._parse(cfg, channel, m, [])
                out.append((m, sig, ""))
            except Exception as e:
                out.append((m, None, str(e)[:120]))
        return out

    async def _extract(self, channel: Channel, chunk: list[TgMessage]) -> list:
        try:
            return await self.ai.extract(channel, chunk)
        except ValueError:
            # A dense stretch can overflow the answer and cut the JSON short: halve and retry.
            if len(chunk) < 40:
                raise
            half = len(chunk) // 2
            return await self._extract(channel, chunk[:half]) + await self._extract(channel, chunk[half:])

    async def _resolve_for_eval(self, ref: str | int) -> Channel:
        if isinstance(ref, int):
            return self.storage.channel(ref)
        try:
            found = await self.tg.resolve(str(ref), join=False)
        except ValueError:
            found = await self.tg.resolve(str(ref), join=True)
        known = self.storage.channel(found.channel_id)
        if known is None:
            # Evaluated but not followed: stored switched off so it shows up in the ranking.
            found.enabled = False
            self.storage.upsert_channel(found)
            known = found
        return known

    async def _calls_from_images(self, channel: Channel, posts: list[TgMessage], min_confidence: float):
        """Read trade levels from pictures with the vision model. One request per picture."""
        calls = []
        for m in posts:
            try:
                photo = await self.tg.photo(m.channel_id, m.message_id)
                if not photo:
                    continue
                sig = await self.ai.parse(channel, m, [], [], [], [photo], m.date)
            except Exception as e:
                log.warning("Picture %s not read: %s", m.key, e)
                continue
            complete = (sig.kind == "new_signal" and sig.side in ("long", "short") and sig.base_asset
                        and sig.stop_loss is not None and sig.take_profits)
            if not complete or sig.confidence < min_confidence or sig.stale_hints:
                continue
            calls.append(HistSignal(
                message_id=m.message_id, base_asset=sig.base_asset, quote_asset=sig.quote_asset or "USDT",
                side=sig.side, entries=[x for x in dict.fromkeys((sig.entry_low, sig.entry_high)) if x is not None],
                stop_loss=sig.stop_loss, take_profits=sig.take_profits, leverage=sig.leverage,
            ))
        return calls

    async def evaluate(self, ref: str | int, progress=None, with_images: bool = False, *, monthly_year: bool = False) -> dict:
        """Measure how the channel's past calls would have played out on real prices."""
        channel = await self._resolve_for_eval(ref)
        channel_id = channel.channel_id
        cfg = self.cfg.for_channel(channel.channel_id, channel.username)
        e = cfg.eval

        async def say(text: str) -> None:
            if progress:
                await progress(text)

        # Full-year evaluation can require more than the default 1,500 messages.
        # Do not treat a limited sample as a full channel history.
        history = await self.tg.history(channel_id, max(e.history_messages, 10000) if monthly_year else e.history_messages)
        if monthly_year:
            cutoff = now() - timedelta(days=365)
            history = [m for m in history if m.date >= cutoff]
        if not history:
            raise RuntimeError("у каналі немає повідомлень")
        for m in history:
            self.storage.save_message(m)
        by_id = {m.message_id: m for m in history}

        # Old forwards are showcases of calls from another channel, shown once the outcome is known.
        lag = timedelta(minutes=e.max_forward_lag_minutes)
        fresh = [m for m in history if m.text.strip() and not (m.fwd_date and m.date - m.fwd_date > lag)]
        showcase = sum(1 for m in history if m.fwd_date and m.date - m.fwd_date > lag
                       and parse_call(m.text) is not None)
        teasers = sum(1 for m in history if TEASER_RE.search(m.text))

        # Some channels post only a TradingView link and keep the levels inside the idea itself.
        linked = [m for m in fresh if find_tv_links(m.links)][-e.tv_ideas_checked:] if self.tv else []
        ideas_by_id: dict[int, TvIdea] = {}
        if linked:
            await say(f"Читаю TradingView-ідеї за посиланнями: {len(linked)}…")
            for m in linked:
                idea, _ = await self.tv.fetch(find_tv_links(m.links)[0], with_image=False)
                if idea.description or idea.page_excerpt:
                    ideas_by_id[m.message_id] = idea
                await asyncio.sleep(1.0)
        tv_calls = 0

        # Rules read template calls; only posts that look like calls but did not parse go to the model.
        kept, leftovers = [], []
        for m in fresh:
            call = parse_call(m.text, m.message_id)
            idea = ideas_by_id.get(m.message_id)
            if call is None and idea is not None:
                found = call_from_idea(idea, m.message_id)
                if found:
                    call = found[0]
                    tv_calls += 1
            if call is not None:
                kept.append(call)
            elif idea is not None and looks_like_call(idea.description):
                # The model and the number check both need the idea text next to the message.
                merged = replace(m, text=f"{m.text}\n\n[TradingView idea] {idea.title}\n{idea.description}")
                by_id[m.message_id] = merged
                leftovers.append(merged)
            elif looks_like_call(m.text):
                leftovers.append(m)
        by_rules = len(kept)
        dropped = {"forward": showcase, "numbers": 0, "unknown": 0, "duplicate": 0}
        dropped_ids: dict[str, list[int]] = {"numbers": [], "unknown": [], "duplicate": []}
        if leftovers:
            await say(f"Правилами прочитано сигналів: {by_rules}. Ще {len(leftovers)} повідомлень віддаю моделі…")
            seen = {c.message_id for c in kept}
            chunks = [leftovers[i:i + e.chunk_messages] for i in range(0, len(leftovers), e.chunk_messages)]
            for chunk in chunks:
                for sig in await self._extract(channel, chunk):
                    m = by_id.get(sig.message_id)
                    if m is None or m not in chunk:
                        reason = "unknown"
                    elif sig.message_id in seen:
                        reason = "duplicate"
                    elif ungrounded(sig.entries + ([sig.stop_loss] if sig.stop_loss is not None else [])
                                    + sig.take_profits, m.text):
                        reason = "numbers"
                    else:
                        seen.add(sig.message_id)
                        kept.append(sig)
                        continue
                    dropped[reason] += 1
                    dropped_ids[reason].append(sig.message_id)
        # Pictures: a screenshot of a position, or a chart with the levels drawn on it, often next
        # to a text that gives only part of the call. Reading costs one model request per picture,
        # so it runs on request, or by itself when the text gave too few calls for a verdict.
        def complete(c) -> bool:
            return c.stop_loss is not None and bool(c.take_profits)

        rules_ids = {c.message_id for c in kept[:by_rules]}
        image_ids: set[int] = set()
        image_posts = 0
        image_auto = False
        have = {c.message_id for c in kept if complete(c)}
        # A picture with no caption at all is a typical case, so this looks past `fresh`.
        recent = [m for m in history if not (m.fwd_date and m.date - m.fwd_date > lag)]
        candidates = [m for m in recent if m.has_photo and not m.reply_to and m.message_id not in have
                      and len(m.text) < MAX_IMAGE_CAPTION and not RESULT_RE.search(m.text)
                      and not TEASER_RE.search(m.text) and not FOREX_RE.search(m.text)]

        # The channel's own "target hit" replies show which posts were calls. When the first such
        # reply lands minutes after the post, the call was published after it had already paid:
        # a reveal of a paid-group trade, which no subscriber of this channel could have taken.
        first_result: dict[int, timedelta] = {}
        for m in history:
            parent = by_id.get(m.reply_to) if m.reply_to else None
            if parent is not None and RESULT_RE.search(m.text) and m.date >= parent.date:
                gap = m.date - parent.date
                if gap < first_result.get(parent.message_id, timedelta.max):
                    first_result[parent.message_id] = gap
        quick = timedelta(minutes=e.reveal_minutes)
        reveal_ids = [m.message_id for m in candidates if first_result.get(m.message_id, timedelta.max) <= quick]
        candidates = [m for m in candidates if m.message_id not in set(reveal_ids)]

        auto_posts = pick_image_posts(candidates, e.image_posts_checked, set(first_result), AUTO_IMAGE_RANK)
        if not with_images and e.images_auto and len(have) < e.min_trades and auto_posts:
            with_images = image_auto = True
        if with_images and candidates:
            posts = auto_posts if image_auto else pick_image_posts(candidates, e.image_posts_checked, set(first_result))
            image_posts = len(posts)
            why = f"текстом знайдено лише {len(have)} сигналів, тому читаю" if image_auto else "читаю"
            await say(f"{why[0].upper() + why[1:]} картинки моделлю: {len(posts)} постів із {len(candidates)}, "
                      f"по одному запиту на кожен…")
            found = await self._calls_from_images(channel, posts, cfg.ai.min_confidence)
            image_ids = {c.message_id for c in found}
            # A picture reading replaces a partial text reading of the same post.
            kept = [c for c in kept if c.message_id not in image_ids] + found
        kept.sort(key=lambda c: c.message_id)
        by_rules = sum(1 for c in kept if c.message_id in rules_ids)
        by_model = len(kept) - by_rules - len(image_ids)

        await say(f"Сигналів для перевірки: {len(kept)} (правилами {by_rules}, моделлю "
                  f"{by_model}, з картинок {len(image_ids)}). Програю кожен по історії цін…")
        horizon = timedelta(days=e.horizon_days)
        moment = now()
        trades: list[Trade] = []
        sources: dict[str, int] = {}
        for sig in kept:
            m = by_id[sig.message_id]
            symbol = symbol_for(sig) or "?"
            if sig.stop_loss is None or not levels_ok(sig):
                trades.append(simulate(sig, symbol, [], 0, True, 0, True))
                continue
            if await self.exchange.tradable_market(symbol) is None:
                trades.append(Trade(sig.message_id, symbol, sig.side, "no_pair"))
                continue
            end = min(m.date + horizon, moment)
            rows, source = await self.prices.candles(symbol, int(m.date.timestamp() * 1000),
                                                     int(end.timestamp() * 1000), e.interval)
            if source:
                sources[source] = sources.get(source, 0) + 1
            trades.append(simulate(sig, symbol, [Candle(*r) for r in rows], cfg.validator.entry_tolerance_pct,
                                   cfg.risk.limit_when_price_ran, e.fee_pct, m.date + horizon <= moment,
                                   cfg.risk.limit_expiry_hours, strict=sig.message_id in image_ids))

        # Calls that give only a direction ("Buying $X here" with a chart): no stop or target to
        # replay, so the check is simply where price went afterwards.
        bare = [(m, call) for m in fresh if not m.reply_to and (call := bare_call(m.text))]
        drifts = []
        if bare:
            sample = bare[-e.bare_calls_checked:]
            await say(f"Сигналів без рівнів: {len(bare)}. Дивлюсь, куди пішла ціна після останніх {len(sample)}…")
            for m, (base, side) in sample:
                symbol = f"{base}-USDT"
                if m.date + timedelta(hours=72) > moment or await self.exchange.tradable_market(symbol) is None:
                    continue
                start = int(m.date.timestamp() * 1000)
                rows, _ = await self.prices.candles(symbol, start, start + 73 * 3_600_000, "1h")
                moved = drift(side, [Candle(*r) for r in rows])
                if moved:
                    drifts.append(moved)

        forwards = sum(1 for m in history if m.fwd_date)
        result = summarize(trades, len(kept), len(history), forwards, history[0].date, history[-1].date,
                           e.min_trades, dropped)
        if result["verdict"] == "no_signals" and bare:
            result["verdict"] = "bare_only"
        # Few calls a subscriber could take, many shown only after the first target was reached.
        after_fact = len(reveal_ids) + showcase + result["counts"].get("late", 0)
        if (result["verdict"] in ("no_signals", "insufficient", "bare_only")
                and after_fact >= e.showcase_min_posts and after_fact >= 3 * result["tested"]):
            result["verdict"] = "showcase"
        result.update(
            bare=summarize_drift(drifts, len(bare), sum(1 for m, _ in bare if m.has_photo)),
            dropped_ids=dropped_ids, by_rules=by_rules, by_model=by_model,
            by_image=len(image_ids), image_posts=image_posts, image_auto=image_auto,
            reveals=len(reveal_ids), reveal_ids=reveal_ids[-20:],
            model_messages=len(leftovers), teasers=teasers, showcase=showcase,
            tv_ideas=len(ideas_by_id), tv_links=len(linked), tv_calls=tv_calls,
            structure=structure(kept), price_sources=sources,
            signals=[sig.model_dump() for sig in kept],
        )
        if monthly_year:
            from .yearly import monthly_rows, format_year
            end_dt = now()
            start_dt = end_dt - timedelta(days=365)
            dates = {m.message_id: m.date for m in history}
            report_rows = monthly_rows(trades, dates, start_dt, end_dt)
            result['monthly_rows'] = report_rows
            result['monthly_report'] = format_year(channel.title, report_rows, history[0].date,
                                                    start_dt, len(history), history[-1].date)
        self.storage.set_evaluation(channel_id, result)
        return result

    async def export(self, ref: str | int) -> tuple[str, bytes]:
        """Raw channel history as one JSON file, for analysis outside the bot. Uses no model calls.
        `ref` is a stored channel id or any channel link; public channels are read without joining."""
        channel = self.storage.channel(ref) if isinstance(ref, int) else None
        if channel is None:
            try:
                channel = await self.tg.resolve(str(ref), join=False)
            except ValueError:
                channel = await self.tg.resolve(str(ref), join=True)
            channel = self.storage.channel(channel.channel_id) or channel
        history = await self.tg.history(channel.channel_id, self.cfg.eval.history_messages)
        data = {
            "channel": {"id": channel.channel_id, "title": channel.title, "username": channel.username},
            "exported_at": now().isoformat(),
            "messages": [
                {
                    "id": m.message_id,
                    "date": m.date.isoformat(),
                    "edit_date": m.edit_date.isoformat() if m.edit_date else None,
                    "fwd_date": m.fwd_date.isoformat() if m.fwd_date else None,
                    "fwd_from": m.fwd_from,
                    "reply_to": m.reply_to,
                    "has_photo": m.has_photo,
                    "links": m.links,
                    "text": m.text,
                }
                for m in history
            ],
            "profile": channel.profile.model_dump() if channel.profile else None,
            "evaluation": channel.evaluation,
        }
        name = re.sub(r"[^A-Za-z0-9_]+", "_", channel.username or str(abs(channel.channel_id))).strip("_")
        return f"export_{name}.json", json.dumps(data, ensure_ascii=False, indent=1).encode()

    async def inspect_idea(self, url: str) -> tuple[str, bytes]:
        """What the bot manages to read from one TradingView link, plus the raw page for debugging."""
        idea, image = await self.tv.fetch(url)
        found = call_from_idea(idea, 0)
        lines = [
            f"TradingView: {url}",
            f"Відповідь сайту: {idea.status}" + (f" ({idea.error})" if idea.error else ""),
            f"Заголовок: {idea.title or '—'}",
            f"Символ: {idea.symbol or '—'} · напрямок на сторінці: {idea.side or 'не знайдено'}",
            f"Опубліковано: {idea.published:%Y-%m-%d %H:%M} UTC" if idea.published else "Опубліковано: не знайдено",
            f"Картинка графіка: {'є' if image else 'немає'}",
            f"Текст ідеї: {len(idea.description)} символів",
            "",
            idea.description[:900] or "(порожньо)",
            "",
            "Сигнал, прочитаний правилами: " + (self.reading_line(call_to_parsed(*found)) if found else "немає"),
        ]
        return "\n".join(lines), self.tv.last_html.encode()

    # helpers

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    @staticmethod
    def _plan_line(p: OrderPlan) -> str:
        parts = [f"{p.symbol} {p.market} {p.side.upper()} {p.order_type} {p.quantity:g} @ {p.entry_price:g}"]
        if p.stop_loss is not None:
            parts.append(f"SL {p.stop_loss:g}")
        if p.take_profit is not None:
            parts.append(f"TP {p.take_profit:g}")
        parts.append(f"x{p.leverage}, маржа {p.margin_usdt:g} USDT, ризик {p.risk_usdt:g} USDT")
        return " | ".join(parts)

    @staticmethod
    def reading_line(sig: ParsedSignal) -> str:
        if sig.kind == "update":
            return f"update: {sig.update_action} {sig.base_asset}".strip()
        if sig.kind != "new_signal":
            return sig.kind
        entry = "ринок"
        if sig.entry_low is not None or sig.entry_high is not None:
            low, high = sig.entry_low or sig.entry_high, sig.entry_high or sig.entry_low
            entry = f"{low:g}" if low == high else f"{low:g}–{high:g}"
        sl = f"{sig.stop_loss:g}" if sig.stop_loss is not None else "—"
        tps = ", ".join(f"{t:g}" for t in sig.take_profits) or "—"
        lev = f" x{sig.leverage}" if sig.leverage else ""
        return f"{symbol_for(sig)} {sig.side.upper()}{lev} | вхід {entry} | SL {sl} | TP {tps}"

    def _report(self, channel: Channel, msg: TgMessage, sig: ParsedSignal, decision: Decision,
                status: str, signal_id: int, response: dict | None, started: float) -> str:
        delay = (now() - msg.date).total_seconds()
        head = {
            "execute": f"угоду відкрито [{status}]",
            "manual": "потрібне підтвердження",
            "reject": "відхилено",
        }[decision.action]
        if status == "error":
            head = f"помилка біржі: {(response or {}).get('error')}"
        lines = [f"{ICON[decision.action]} #{signal_id} {channel.title}: {head}",
                 f"Прочитано: {self.reading_line(sig)}"]
        if decision.plan:
            lines.append(f"Ордер: {self._plan_line(decision.plan)}")
        if decision.reasons:
            lines.append("Причини: " + "; ".join(decision.reasons))
        lines.append(f"Впевненість {sig.confidence:.2f} · обробка {self._ms(started)} мс · "
                     f"від публікації {delay:.1f} с · режим {self.cfg.trading_mode}")
        origin = f"Джерело: {self.link(channel, msg.message_id)}"
        if msg.fwd_date:
            origin += f" (переслано з {msg.fwd_from or 'прихованого джерела'}, оригінал {msg.fwd_date:%d.%m %H:%M} UTC)"
        lines.append(origin)
        tv = find_tv_links(msg.links)
        if tv:
            lines.append("TradingView: " + " ".join(tv[:2]))
        lines.append(f"\n{msg.text[:300]}")
        return "\n".join(lines)

    async def _notify(self, text: str, actions: Actions | None = None) -> None:
        log.info(text)
        if self.notifier:
            try:
                await self.notifier.send(text, actions)
            except Exception:
                log.exception("Notification failed")
