from __future__ import annotations

import io
import asyncio
import logging
import re

from telethon import Button, TelegramClient, events

from .tv_registry import TvRegistry, parse_profile
import tv_author_settings as author_settings
from .tv_scanner import discover, ScanBlocked
from .audit import analyze
from .tv_review import review as review_tv
from .tv_missed import skipped_rows
from . import tv_chart_audit, tv_entry_audit
from .tv_reconcile import reconcile as reconcile_tv_candidates
from .tv_entry import verify as verify_tv_entry, resolve_symbol as resolve_tv_symbol
from .tv_performance import evaluate as evaluate_tv_performance, summary as summarize_tv_performance
from .monthly import summary as monthly_summary
from .watch import WatchManager
from .models import Channel
from .pipeline import RULES_REASON, Actions, Pipeline
from .storage import Storage
from .telegram import INVITE_RE, PUBLIC_RE

log = logging.getLogger("signalbot.bot")

LIMIT = 4000
KIND_ICON = {"new_signal": "🟢", "update": "🔁", "result": "🏁", "demo_or_promo": "📣", "analysis": "📊", "noise": "·"}
STATUS_UA = {
    "shadow": "відкрито (shadow)", "executed": "відкрито", "awaiting": "чекає підтвердження",
    "rejected": "відхилено", "declined": "відхилено вручну", "error": "помилка біржі",
    "closed": "закрито", "cancelled": "скасовано", "noted": "оновлення", "approving": "виконується",
}
HELP = """Керування:
/add <лінк> — додати канал (або просто надішліть лінк t.me/…)
/eval <лінк> — оцінити канал на історії цін, не додаючи його до відстеження
/evalimg <лінк> — те саме, але картинки читаються завжди (/eval читає їх сам, коли текстом сигналів мало)
/evalmonths <лінк> — звіт по місяцях за останні 365 днів (модельний)
/export <лінк> — вивантажити історію каналу файлом
/tv <лінк> — показати, що бот читає з TradingView-ідеї
/tvadd <профіль> — додати автора й увімкнути автоматичний моніторинг
/tvmonitors — автори під автоматичним моніторингом
/tvunmonitor <автор> — вимкнути моніторинг (історія зберігається)
/tvmonitor <автор> — знову ввімкнути моніторинг
/tvauthors — список збережених авторів
/tvidea <автор> <ідея> — додати посилання на ідею
/tvideas <автор> — список отриманих ідей
/tvanalyze <автор> — аналіз 1 збереженої ідеї без ордерів
/tvscan <профіль> 365 — пошук і аналіз доступних публічних ідей
/tvscanstatus — стан фонового сканування
/tvcontinue <автор> 50 — продовжити AI-перевірку
/tvmonths <автор> — помісячне покриття й класифікація
/tvaudit <автор> [сторінка] — 5 збережених AI-класифікацій
/tvauditidea <ID> — повний збережений звіт
/tvreview <автор> 50 — повторна класифікація, без ордерів
/tvreviewstatus <автор> — прогрес другого аудиту
/tvreviewidea <ID> — другий аудит конкретної ідеї
/tventries <автор> — прямі сигнали та зіставлення Entry з історичною ціною
/tvperformance <автор> — TP1/SL replay до 30 днів, MFE/MAE, без ордерів
/tvmissed <автор> [сторінка] — аудит пропущених ідей, без Gemini
/tvmissedidea <ID> — ознаки й причини пропуску
/tvchartaudit <автор> 20 — повторний перегляд повного тексту й графіка (Gemini)
/tvchartstatus <автор> — прогрес доказового аудиту
/tvchartidea <ID> — докази з графіка й тексту
/tvcandidates <автор> — порівняти chart-кандидатів з минулими постами та цінами
/tventryaudit <автор> [ліміт] — точковий Gemini-аудит пропущених Entry, тільки нові
/tventrystatus <автор> — результати точкового аудиту
/tventryidea <ID> — деталі знайденого Entry
/tvstatus — стан бази TradingView
/analyze <лінк> — Gemini аналіз Telegram-поста або TradingView БЕЗ ордерів
/watch <лінк> — 24 години відстежувати закриті свічки, без ордерів
/watches — список спостережень
/unwatch <id> — скасувати спостереження
/channels — канали: увімкнути, вимкнути, перевірити, профіль
/signals — останні сигнали з джерелами
/rank — рейтинг каналів за результатом на історії цін
/stats — статистика по каналах за 30 днів
/monthstats 2026-10 — місячна статистика перевірених TradingView-ідей
/status — режим, модель, що зараз слухається"""


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def fmt_channel(c: Channel) -> str:
    p = c.profile
    state = "🟢" if c.enabled else "⚪️"
    name = f"{c.title} (@{c.username})" if c.username else c.title
    extra = f" · сигналів в історії: {p.signals_found} · ринок: {p.default_market}" if p else ""
    return f"{state} {name}{extra}"


def fmt_profile(c: Channel) -> str:
    p = c.profile
    if not p:
        return f"{c.title}: профілю ще немає"
    lines = [
        fmt_channel(c),
        f"Придатний для автоторгівлі: {'так' if p.tradable else 'ні'}",
        f"Рівні на картинках: {'так' if p.levels_in_images else 'ні'} · TradingView: {'так' if p.uses_tradingview else 'ні'}"
        f" · сигнал у кількох повідомленнях: {'так' if p.multi_message_signals else 'ні'}",
        "", f"Що постить: {p.summary}", "", f"Формат сигналу: {p.signal_format}",
        "", f"Оновлення: {p.update_patterns}", "", f"Не сигнали: {p.non_signal_patterns}",
    ]
    if p.examples:
        lines += ["", "Приклади:"]
        lines += [f"• [{e.kind}] #{e.message_id}: {e.excerpt[:120]}\n   {e.explanation}" for e in p.examples]
    if p.notes:
        lines += ["", f"Примітки: {p.notes}"]
    return "\n".join(lines)


VERDICT = {
    "good": "✅ Вартий уваги: результат стабільно вище нуля",
    "neutral": "🟡 Не доведено: результат не відрізнити від нуля",
    "bad": "⛔ Не вартий уваги: результат стабільно нижче нуля",
    "insufficient": "⚪️ Замало перевірених сигналів для висновку",
    "no_signals": "🚫 У каналі немає сигналів, які можна взяти в угоду",
    "not_on_exchange": "🚫 Сигнали не про крипту або таких пар немає на BingX",
    "bare_only": "🖼 Лише сигнали без рівнів: оцінено тільки напрямок, не угоди",
    "showcase": "🎭 Канал-вітрина: сигнали показують уже після того, як ціна дійшла до цілі, взяти їх вчасно неможливо",
}
STRATEGY_UA = {
    "first": "вихід на першій цілі",
    "middle": "вихід на середній цілі",
    "last": "вихід на останній цілі",
    "ladder": "рівними частинами на кожній цілі",
    "ladder_be": "частинами, стоп у беззбиток після першої цілі",
    "ladder_trail": "частинами, стоп підтягується за цілями",
    "last_be": "до останньої цілі, стоп у беззбиток після першої",
}
SKIP_UA = {
    "no_pair": "пари немає на BingX", "no_stop": "без стоп-лосу", "invalid": "рівні суперечать одне одному",
    "stale": "ціна вже була за стопом", "late": "показано, коли ціна вже дійшла до першої цілі", "missed": "ціна втекла від входу",
    "not_filled": "вхід не спрацював за добу", "open": "ще не завершились", "no_data": "немає історії цін",
}
SOURCE_UA = {"binance_futures": "Binance ф'ючерси", "binance_spot": "Binance спот", "bingx": "BingX"}


def fmt_evaluation(c: Channel) -> str:
    ev = c.evaluation
    if not ev:
        return f"{c.title}: оцінки ще немає. Натисніть «📊 Оцінити»."
    lines = [
        f"📊 Оцінка «{c.title}»",
        VERDICT[ev["verdict"]],
        "",
        f"Період: {ev['period_start'][:10]} — {ev['period_end'][:10]} ({ev['days']:g} дн., {ev['messages']} повідомлень)",
        f"Сигналів знайдено: {ev['found']} (≈ {ev['per_day']:g} на день)",
    ]
    if "by_rules" in ev:
        lines.append(f"Прочитано правилами: {ev['by_rules']} · моделлю: {ev['by_model']} "
                     f"(моделі показано {ev['model_messages']} повідомлень із {ev['messages']})")
    extra = []
    if ev.get("showcase"):
        extra.append(f"вітринних сигналів, пересланих з іншого каналу після відпрацювання: {ev['showcase']}")
    if ev.get("reveals"):
        extra.append(f"постів-вітрин, де звіт про взяту ціль з'явився за кілька хвилин після самого поста: {ev['reveals']}")
    if ev.get("teasers"):
        extra.append(f"постів із прихованими рівнями «тільки для VIP»: {ev['teasers']}")
    if extra:
        lines.append("Не сигнали — " + "; ".join(extra))
    if ev.get("image_posts"):
        auto = " (автоматично, бо текстом сигналів мало)" if ev.get("image_auto") else ""
        lines.append(f"Картинки{auto}: переглянуто моделлю {ev['image_posts']} постів, "
                     f"сигналів знайдено на них: {ev['by_image']}")
    if ev.get("tv_links"):
        lines.append(f"TradingView: посилань {ev['tv_links']}, сторінок прочитано {ev['tv_ideas']}, "
                     f"сигналів знайдено в ідеях: {ev['tv_calls']}")

    st = ev.get("structure") or {}
    if st:
        lines += [
            "",
            f"Як виглядає типовий сигнал ({st['calls']} шт.):",
            f"• стоп за {st['sl_pct']:g}% від входу, перша ціль за {st['tp1_pct']:g}%",
            f"• перша ціль дає {st['rr_first']:g}R, остання {st['rr_last']:g}R",
            f"• щоб вийти в нуль на першій цілі, до неї мають доходити {st['breakeven_first']}% сигналів",
        ]
        if st["leverage"]:
            lines.append(f"• плече в сигналах: x{st['leverage']:g}")
        if st["stop_beyond_liquidation"]:
            lines.append(f"• у {st['stop_beyond_liquidation']} сигналах стоп стоїть далі, ніж ліквідація при вказаному "
                         "плечі: позицію закриє біржа раніше за стоп")

    bare = ev.get("bare") or {}
    if bare.get("total"):
        lines += ["", f"Сигнали без рівнів («Buying $X here», «LONG SETUP» + графік): {bare['total']}, "
                      f"з картинкою {bare['with_photo']}"]
        if bare["tested"]:
            lines += [
                f"• перевірено останніх: {bare['tested']}",
                f"• через 24 год ціна в бік сигналу: {bare['h24']:+g}% (у плюсі {bare['h24_up']}%)",
                f"• через 72 год: {bare['h72']:+g}% (у плюсі {bare['h72_up']}%)",
                f"• за 72 год типово доходило до {bare['best']:+g}% у бік сигналу і до {bare['worst']:+g}% проти",
            ]
        else:
            lines.append("• перевірити по цінах не вдалося (монет немає на BingX або сигнали надто свіжі)")

    lines += ["", f"Перевірено по історії цін: {ev['tested']}"]
    skipped = [f"{SKIP_UA[k]}: {n}" for k, n in ev["counts"].items() if k in SKIP_UA and n]
    if skipped:
        lines.append("Не враховано — " + "; ".join(skipped))
    if ev.get("price_sources"):
        lines.append("Ціни: " + ", ".join(f"{SOURCE_UA.get(k, k)} ({n})" for k, n in ev["price_sources"].items()))
    if not ev["tested"]:
        return "\n".join(lines)

    n = ev["tested"]
    lines += [
        f"Дійшли до першої цілі: {ev['tp1_reached']} з {n} ({ev['tp1_reached'] / n:.0%}) · до всіх цілей: "
        f"{ev['all_tps_reached']} · закінчились стопом: {ev['stopped']}",
        "",
        "Результат, якби бот брав кожен сигнал (1R = сума, якою ризикуєш в одній угоді):",
    ]
    for name, s_ in ev["strategies"].items():
        mark = " ← найкраще" if name == ev["best"] else ""
        span = f", імовірний діапазон {s_['low_r']:+.2f}…{s_['high_r']:+.2f}R" if "low_r" in s_ else ""
        lines.append(f"• {STRATEGY_UA[name]}: {s_['total_r']:+g}R за {n} угод, у середньому {s_['avg_r']:+.2f}R"
                     f"{span}, у плюс {s_['wins']}{mark}")
    best = ev["strategies"][ev["best"]]
    lines += [
        "",
        f"При ризику 1% депозиту на угоду найкращий варіант дав би {best['total_r']:+.1f}% за {ev['days']:g} дн.",
        "",
        "Як читати: комісія врахована; якщо стоп і ціль в одній 5-хвилинній свічці, зараховано стоп. "
        "Вердикт «вартий уваги» ставиться, лише коли результат найкращого способу виходу вище нуля "
        "із запасом на те, що цей спосіб обрано з семи вже після перевірки. "
        "Видалені адміном сигнали в історію не потрапляють, тому реальний результат може бути гіршим.",
    ]
    return "\n".join(lines)


def fmt_rank(channels: list[Channel]) -> str:
    rated = [c for c in channels if c.evaluation]
    if not rated:
        return "Ще немає оцінених каналів. Надішліть /eval <лінк>."
    scored = ("good", "neutral", "bad")

    def score(c: Channel) -> float:
        ev = c.evaluation
        if ev["verdict"] not in scored:
            return float("-inf")
        return ev["strategies"][ev["best"]]["avg_r"]

    lines = ["Рейтинг каналів (середній результат на угоду за найкращого способу виходу)", ""]
    for i, c in enumerate(sorted(rated, key=score, reverse=True), 1):
        ev = c.evaluation
        icon, _, label = VERDICT[ev["verdict"]].partition(" ")
        if ev["verdict"] not in scored:
            lines.append(f"{i}. {icon} {c.title} — {label[0].lower() + label[1:]} (сигналів {ev['found']}, "
                         f"перевірено {ev['tested']})")
            continue
        best = ev["strategies"][ev["best"]]
        lines.append(f"{i}. {icon} {c.title} — {best['avg_r']:+.2f}R на угоду, {best['total_r']:+g}R за "
                     f"{ev['tested']} угод · {STRATEGY_UA[ev['best']]}")
    missing = [c.title for c in channels if not c.evaluation]
    if missing:
        lines += ["", "Без оцінки: " + ", ".join(missing)]
    return "\n".join(lines)


def fmt_check(pipeline: Pipeline, channel: Channel, results) -> str:
    if not results:
        return f"{channel.title}: немає повідомлень для перевірки"
    lines = [f"Перевірка «{channel.title}»: як модель читає останні {len(results)} повідомлень. Угоди не відкриваються.", ""]
    for msg, sig, note in results:
        link = pipeline.link(channel, msg.message_id)
        excerpt = " ".join(msg.text.split())[:110] or "(без тексту)"
        if sig is None:
            lines += [f"⚠️ помилка моделі: {note}", f"   «{excerpt}»", f"   {link}", ""]
            continue
        flags = []
        if sig.numbers_from_image:
            flags.append("з картинки")
        if sig.stale_hints:
            flags.append("ознаки старого: " + "; ".join(sig.stale_hints[:2]))
        how = " · правилами, без моделі" if sig.reason == RULES_REASON else ""
        lines.append(f"{KIND_ICON.get(sig.kind, '·')} {pipeline.reading_line(sig)} · впевненість {sig.confidence:.2f}{how}")
        if flags:
            lines.append("   " + " · ".join(flags))
        lines += [f"   «{excerpt}»", f"   {link}", ""]
    return "\n".join(lines).rstrip()


def fmt_stats(storage: Storage) -> str:
    stats = storage.channel_stats()
    channels = storage.channels()
    if not channels:
        return "Каналів ще немає. Додайте: /add <лінк>"
    lines = ["Статистика за 30 днів", ""]
    for c in channels:
        st = stats.get(c.channel_id)
        lines.append(fmt_channel(c))
        if not st:
            lines += ["   ще немає оброблених повідомлень", ""]
            continue
        lines.append(f"   прочитано моделлю: {st['parsed']} · сигналів: {st['signals']} · оновлень: {st['updates']}")
        lines.append(f"   відкрито: {st['opened']} · чекає: {st['awaiting']} · відхилено: {st['rejected']}"
                     f" · помилок: {st['errors']} · обробка ≈ {st['latency']:.0f} мс")
        lines.append("")
    return "\n".join(lines).rstrip()


def fmt_signals(pipeline: Pipeline, rows) -> str:
    if not rows:
        return "Сигналів ще не було"
    titles = {c.channel_id: c for c in pipeline.storage.channels()}
    lines = []
    for r in rows:
        c = titles.get(r["channel_id"])
        title = c.title if c else str(r["channel_id"])
        link = pipeline.link(c, r["message_id"]) if c else ""
        what = f"{r['symbol'] or '?'} {(r['side'] or '').upper()}" + (f" @ {r['entry']:g}" if r["entry"] else "")
        if r["kind"] == "update":
            what = f"оновлення {r['symbol'] or ''}"
        lines.append(f"#{r['id']} {r['created_at'][5:16].replace('T', ' ')} · {title}")
        lines.append(f"   {what} — {STATUS_UA.get(r['status'], r['status'])}")
        lines.append(f"   {link}")
    return "\n".join(lines)


class Bot:
    """Telegram bot that is the only user interface: control, reports and approvals."""

    def __init__(self, api_id: int, api_hash: str, token: str, owner_id: int, session: str = "signalbot_bot"):
        self.client = TelegramClient(session, api_id, api_hash)
        self.token = token
        self.owner_id = owner_id
        self.pipeline: Pipeline | None = None
        self.watches = None
        self.tv_registry = None
        self.tv_scan_task = None
        self.tv_scan_state = "ще не запускали"

    async def start(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline
        self.tv_registry = TvRegistry(pipeline.storage.db)
        await self.client.start(bot_token=self.token)
        self.client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self._on_button, events.CallbackQuery())
        self.watches = WatchManager(self, pipeline)
        self.watches.start()

    async def stop(self) -> None:
        if self.tv_scan_task and not self.tv_scan_task.done():
            self.tv_scan_task.cancel()
            try: await self.tv_scan_task
            except asyncio.CancelledError: pass
        if self.watches:
            await self.watches.stop()
        await self.client.disconnect()

    async def send(self, text: str, actions: Actions | None = None) -> None:
        parts = chunks(text)
        for i, part in enumerate(parts):
            buttons = None
            if actions and i == len(parts) - 1:
                flat = [Button.inline(label, data.encode()) for label, data in actions]
                buttons = [flat[j:j + 2] for j in range(0, len(flat), 2)]
            await self.client.send_message(self.owner_id, part, buttons=buttons, link_preview=False)

    async def send_file(self, name: str, data: bytes, caption: str) -> None:
        file = io.BytesIO(data)
        file.name = name
        await self.client.send_file(self.owner_id, file, caption=caption, force_document=True)

    async def _run_tv_scan(self, handle: str, days: int) -> None:
        try:
            async def progress(page,count):
                self.tv_scan_state=f'@{handle}: сторінок {page}, кандидатів {count}'
            data=await discover(handle,days,on_page=progress)
            self.tv_registry.add_author(handle)
            verified=0
            for iid,(url,published,author_verified) in data['ideas'].items():
                # The feed has author metadata; HTML profile candidates must be
                # verified by user separately to avoid associating related authors.
                if not author_verified: continue
                self.tv_registry.add_idea(handle,url)
                meta=data['metadata'].get(iid,{})
                self.tv_registry.save_metadata(iid,published,meta.get('title',''),meta.get('market',''),meta.get('owner',''))
                verified+=1
            self.tv_scan_state=f'@{handle}: {verified} авторських ідей, {data["pages"]} сторінок; аналіз почався'
            if not verified:
                await self.send('⚠️ Нових ідей із підтвердженим автором не отримано. '+ '; '.join(data['notes'][:2])+'\nАвтоматичний збір повної історії недоступний через цей інтерфейс.')
                return
            analyzed, failed, blocked = await self._analyze_tv_batch(handle,50)
            stats=self.tv_registry.audit_status(handle)
            self.tv_scan_state=f'@{handle}: завершено · {stats["stored"]} збережено · {stats["analyzed"]} аналізів · {failed} помилок'
            await self.send(f'📊 @{handle} · скан завершено\nFeed: {data.get("reported_count")} ідей; {data["pages"]}/{data.get("reported_pages")} сторінок\nЗа останні {days} днів: {verified} · старі/без дати: {data["excluded_old"]}\nAI-звітів: {stats["analyzed"]}/{stats["stored"]} (цього запуску: {analyzed}; помилок: {failed})\nНайстаріша дата: {(stats["earliest"] or "—")[:10]}\nПокриття: не підтверджене\n/tvcontinue {handle} · /tvmonths {handle}')
        except asyncio.CancelledError:
            raise
        except ScanBlocked as exc:
            self.tv_scan_state=f'@{handle}: BLOCKED — {exc}'
            await self.send('⛔ TradingView: '+str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.tv_scan_state=f'@{handle}: FAILED {type(exc).__name__}: {str(exc)[:120]}'
            await self.send('⚠️ Сканер: '+self.tv_scan_state)

    async def _analyze_tv_batch(self, handle: str, limit: int = 20):
        analyzed=failed=0;blocked=False
        for row in self.tv_registry.pending(handle,limit):
            try:
                report=await analyze(self.pipeline,row['url'])
                self.tv_registry.save_scan_report(row['idea_id'],report)
                analyzed+=1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed+=1
                if '403' in str(exc) or '429' in str(exc):
                    blocked=True;break
                log.warning('TV analysis %s failed: %s',row['idea_id'],str(exc)[:180])
            self.tv_scan_state=f'@{handle}: AI {analyzed}/{limit}, помилок {failed}'
            await asyncio.sleep(3)
        return analyzed,failed,blocked

    async def _continue_tv(self,handle,limit):
        try:
            count,fail,blocked=await self._analyze_tv_batch(handle,limit)
            stats=self.tv_registry.audit_status(handle)
            self.tv_scan_state=f'@{handle}: AI {stats["analyzed"]}/{stats["stored"]}'
            await self.send(f'🧠 @{handle}: +{count} AI-звітів, помилок {fail}\nЗагалом {stats["analyzed"]}/{stats["stored"]}; залишилося {stats["stored"]-stats["analyzed"]}'+ ('\n⛔ Обмеження доступу' if blocked else '')+f'\n/tvcontinue {handle} · /tvmonths {handle}')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.tv_scan_state=f'FAILED: {type(exc).__name__}: {str(exc)[:120]}'
            await self.send('⚠️ '+self.tv_scan_state)

    async def _tv_candidates_batch(self,handle):
        try:
            result=await reconcile_tv_candidates(self.pipeline.prices,self.tv_registry.db,handle)
            for part in chunks(result): await self.send(part)
        except Exception as exc:
            log.exception('Candidate reconciliation failed')
            await self.send(f'⚠️ Не вдалося зіставити кандидатів: {type(exc).__name__}. База не змінена.')

    async def _tv_performance_batch(self,handle):
        import json
        try:
            self.tv_registry.ensure_reviews()
            rows=self.tv_registry.db.execute("""SELECT r.idea_id,r.review_json FROM tv_v27_reviews r
               JOIN tv_ideas i ON i.idea_id=r.idea_id WHERE i.handle=?
               ORDER BY i.added_at DESC""",(handle,)).fetchall()
            selected=[]
            for row in rows:
                d=json.loads(row['review_json'])
                if d.get('classification',{}).get('intent')=='DIRECT_SIGNAL':
                    d['idea_id']=row['idea_id']; selected.append(d)
            if not selected:
                await self.send(f'Для @{handle} немає прямих сигналів v2.7.');return
            out=[]
            for d in selected:
                result=await evaluate_tv_performance(self.pipeline.prices,d)
                result.setdefault('id',d['idea_id'])
                if not result.get('month'):
                    result['month']=(d.get('published_at') or 'unknown')[:7]
                result.setdefault('symbol',resolve_tv_symbol(d) or '?')
                out.append(result)
            await self.send(summarize_tv_performance(out))
        except Exception:
            log.exception('TradingView performance failed')
            await self.send('⚠️ Помилка історичного replay. Перевір Terminal; старі аудити не змінено.')

    async def _review_tv_batch(self,handle,limit):
        ok=fail=0
        for row in self.tv_registry.pending_reviews(handle,limit):
            try:
                data=await review_tv(self.pipeline,row['url'])
                self.tv_registry.save_review(row['idea_id'],data)
                ok+=1
            except asyncio.CancelledError: raise
            except Exception as exc:
                fail+=1
                log.warning('Second audit %s: %s',row['idea_id'],str(exc)[:200])
                if '403' in str(exc) or '429' in str(exc): break
            self.tv_scan_state=f'@{handle}: повторний аудит {ok}/{limit}, помилок {fail}'
            await asyncio.sleep(3)
        total,done,counts=self.tv_registry.review_stats(handle)
        self.tv_scan_state=f'@{handle}: другий аудит {done}/{total}'
        await self.send(f'🧾 @{handle}: +{ok} повторно перевірено, помилок {fail}\nЗагалом {done}/{total}\n/tvreviewstatus {handle}')

    async def _focused_entry_batch(self,handle,limit):
        ok=fail=0; stopped=''
        pending=tv_entry_audit.pending(self.tv_registry.db,handle,limit)
        if not pending:
            await self.send(f'🔍 @{handle}: нових кандидатів без Entry немає. /tventrystatus {handle}')
            return
        for rec in pending:
            try:
                evidence=await tv_entry_audit.analyze(self.pipeline,rec)
                tv_entry_audit.save(self.tv_registry.db,rec['idea_id'],evidence)
                ok+=1
            except asyncio.CancelledError: raise
            except Exception as exc:
                fail+=1
                log.warning('Focused entry audit %s: %s',rec['idea_id'],str(exc)[:250])
                if any(code in str(exc) for code in ('402','403','429','RESOURCE_EXHAUSTED')):
                    stopped='Gemini quota/billing error';break
            self.tv_scan_state=f'Focused entry @{handle}: {ok}/{len(pending)}; errors {fail}'
            await asyncio.sleep(3)
        self.tv_scan_state=f'Focused entry @{handle}: done {ok}, errors {fail}'
        await self.send(f'🔍 @{handle}: перевірено {ok}, помилок {fail}.'+
           (f'\n⛔ {stopped}' if stopped else '')+f'\n/tventrystatus {handle}')

    async def _chart_audit_batch(self, handle, limit):
        ok=fail=0; stop_reason=''
        for row in tv_chart_audit.pending(self.tv_registry.db,handle,limit):
            try:
                evidence=await tv_chart_audit.analyze(self.pipeline,row['url'])
                tv_chart_audit.save(self.tv_registry.db,row['idea_id'],evidence)
                ok+=1
            except asyncio.CancelledError: raise
            except Exception as exc:
                fail+=1
                log.warning('Chart evidence %s: %s',row['idea_id'],str(exc)[:250])
                if any(code in str(exc) for code in ('402','403','429','RESOURCE_EXHAUSTED')):
                    stop_reason='AI quota/billing/access blocked';break
            self.tv_scan_state=f'Chart audit @{handle}: {ok}/{limit}, failed {fail}'
            await asyncio.sleep(3)
        total,done,counts=tv_chart_audit.summary(self.tv_registry.db,handle)
        await self.send(f'🖼 @{handle}: chart audit {done}/{total} (+{ok}, errors {fail})'+
          ('\n⛔ '+stop_reason if stop_reason else '')+f'\n/tvchartstatus {handle}')

    async def _export(self, ref) -> None:
        await self.send("Вивантажую історію каналу…")
        name, data = await self.pipeline.export(ref)
        await self.send_file(name, data, f"{name} · {len(data) // 1024} КБ")

    def _channel_buttons(self, c: Channel) -> Actions:
        toggle = ("⏸ Вимкнути", f"off:{c.channel_id}") if c.enabled else ("▶️ Увімкнути", f"on:{c.channel_id}")
        return [("📊 Оцінити", f"eval:{c.channel_id}"), ("🔍 Перевірити", f"check:{c.channel_id}"),
                ("📄 Профіль", f"profile:{c.channel_id}"), ("📤 Експорт", f"export:{c.channel_id}"), toggle]

    async def _on_message(self, event) -> None:
        if event.sender_id != self.owner_id:
            await event.respond(f"Доступ закрито. Ваш Telegram id: {event.sender_id}\n"
                                "Якщо це ваш бот, впишіть цей id в OWNER_ID у .env і перезапустіть.")
            return
        text = (event.raw_text or "").strip()
        try:
            await self._dispatch(text, event)
        except Exception as e:
            log.exception("Command failed: %s", text)
            await self.send(f"⚠️ {e}")

    async def _dispatch(self, text: str, event=None) -> None:
        p = self.pipeline
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        if not text.startswith("/") and (INVITE_RE.search(text) or PUBLIC_RE.search(text)):
            cmd, arg = "/add", text

        if cmd in ("/start", "/help"):
            await self.send(HELP)
        elif cmd == "/add":
            if not arg:
                await self.send("Надішліть лінк на канал: /add https://t.me/назва")
                return
            await self.send("Вступаю в канал і читаю історію, це до хвилини…")
            channel = await p.onboard(arg)
            if channel.profile is None:
                await self.send(f"⚠️ Профіль каналу не побудовано, модель недоступна: {p.onboard_error}\n"
                                "Канал додано вимкненим. Оцінка все одно працює: шаблонні сигнали читаються без моделі.",
                                self._channel_buttons(channel))
            else:
                note = "" if channel.enabled else "\n\n⚪️ Канал вимкнено: модель не вважає сигнали придатними для автоторгівлі."
                await self.send(fmt_profile(channel) + note, self._channel_buttons(channel))
            await self._evaluate(channel.channel_id)
        elif cmd == "/eval":
            if not arg:
                await self.send("Надішліть лінк на канал: /eval https://t.me/назва")
                return
            await self._evaluate(arg)
        elif cmd == "/evalimg":
            if not arg:
                await self.send("Надішліть лінк на канал: /evalimg https://t.me/назва")
                return
            await self._evaluate(arg, with_images=True)
        elif cmd == "/evalmonths":
            if not arg:
                await self.send('Використання: /evalmonths https://t.me/канал')
                return
            await self.send('📊 Перевіряю канал за останні 365 днів; звіт залежить від покриття повідомлень і доступних свічок.')
            result = await p.evaluate(arg, progress=self.send, monthly_year=True)
            await self.send(result.get('monthly_report', 'Немає помісячного звіту'))
        elif cmd == "/analyze":
            replied = await event.get_reply_message() if event and event.is_reply else None
            await self.send("🔎 Аналізую повідомлення, TradingView та графіки через AI. Ордери вимкнені для цієї команди.")
            report = await analyze(p, arg, replied)
            await self.send(report)
        elif cmd == "/watch":
            if not arg:
                await self.send("Використання: /watch https://t.me/c/1651003432/1030")
                return
            await self.send("🔍 Перевіряю джерело для спостереження; жодних ордерів.")
            _, sig, symbol, market = await analyze(p, arg, return_details=True)
            if not symbol or sig.side not in ("long", "short"):
                await self.send("Не вдалося визначити пару та напрямок LONG/SHORT. Спостереження не створено.")
                return
            wid = self.watches.add(arg, symbol, market, sig.side, sig)
            await self.send(f"👁 Watch #{wid}: {symbol} {sig.side.upper()} · 24 год.\n"
                            "Раз на хвилину перевіряю нові закриті 15m свічки. "
                            "Стежу за умовами сценарію з оригінального повідомлення; без точних рівнів лише WATCH. "
                            "Це не підтверджений вхід і НЕ ордер. /watches · /unwatch <id>")
        elif cmd == "/watches":
            rows=self.watches.list()
            await self.send("Активні спостереження:\n" + "\n".join(
                f"#{r['id']} {r['symbol']} {r['side']} до {r['expires_at'][:16]} UTC · сповіщено: {'так' if r['alerted'] else 'ні'}" for r in rows)
                if rows else "Активних спостережень немає.")
        elif cmd == "/unwatch":
            if not arg.isdecimal():
                await self.send("Використання: /unwatch <id>")
            else:
                await self.send("Спостереження скасовано." if self.watches.remove(int(arg)) else "Такого спостереження немає.")
        elif cmd == "/tvscan":
            parts=arg.split()
            if not parts or len(parts)>2:
                await self.send("Використання: /tvscan https://www.tradingview.com/u/Altsignals/ 365")
                return
            try:
                handle,_=parse_profile(parts[0])
                days=int(parts[1]) if len(parts)>1 else 365
                if not 1<=days<=365: raise ValueError('Вкажи 1–365 днів')
            except (ValueError,IndexError) as exc:
                await self.send(f"⚠️ {exc}");return
            if self.tv_scan_task and not self.tv_scan_task.done():
                await self.send("⏳ Інший TradingView-скан уже виконується. /tvscanstatus")
                return
            self.tv_scan_state=f'@{handle}: запуск, {days} днів'
            self.tv_scan_task=asyncio.create_task(self._run_tv_scan(handle,days))
            await self.send(f"🔎 Запустив скан @{handle} за {days} днів у фоні. /tvscanstatus\nЗапити обмежено; блокування або помилка зупинять скан. Повноту року поки не гарантовано.")
        elif cmd == "/tvcontinue":
            parts=arg.split()
            if not parts or len(parts)>2:
                await self.send('Використання: /tvcontinue Altsignals 50');return
            try:
                handle,_=parse_profile(parts[0]);limit=int(parts[1]) if len(parts)>1 else 20
                if limit<1 or limit>50:raise ValueError('Максимум 50 за запуск')
            except ValueError as exc:
                await self.send(f'⚠️ {exc}');return
            if self.tv_scan_task and not self.tv_scan_task.done():
                await self.send('⏳ Завдання вже виконується. /tvscanstatus');return
            self.tv_scan_task=asyncio.create_task(self._continue_tv(handle,limit))
            await self.send(f'🧠 Продовжую аналіз @{handle}, до {limit} нових ідей. /tvscanstatus')
        elif cmd == "/tvreview":
            parts=arg.split()
            if not parts or len(parts)>2:
                await self.send('Використання: /tvreview Altsignals 50');return
            try:
                handle,_=parse_profile(parts[0]);limit=int(parts[1]) if len(parts)>1 else 10
                if not 1<=limit<=50:raise ValueError('Допустимо 1–50 ідей за запуск')
            except ValueError as exc:
                await self.send(f'⚠️ {exc}');return
            if self.tv_scan_task and not self.tv_scan_task.done():
                await self.send('⏳ Інша задача вже виконується');return
            self.tv_scan_task=asyncio.create_task(self._review_tv_batch(handle,limit))
            await self.send(f'🔎 Повторно перевіряю до {limit} ідей @{handle}; історичні звіти не перезаписуються. Без ордерів.')
        elif cmd == "/tvreviewstatus":
            if not arg:
                await self.send('Використання: /tvreviewstatus Altsignals');return
            total,done,counts=self.tv_registry.review_stats(arg)
            lines=[f'🧾 @{arg.lower()}: повторний аудит {done}/{total}',
                   'Категорії аудиту v2.7 (не статистика угод):']
            lines.extend(f'{kind}: {count}' for kind,count in sorted(counts.items()))
            await self.send('\n'.join(lines))
        elif cmd == "/tventries":
            if not arg:
                await self.send('Використання: /tventries altsignals');return
            try:handle,_=parse_profile(arg)
            except ValueError as exc:
                await self.send(f'⚠️ {exc}');return
            import json
            db=self.tv_registry.db
            self.tv_registry.ensure_reviews()
            rows=db.execute("""SELECT r.idea_id,r.review_json FROM tv_v27_reviews r
              JOIN tv_ideas i ON i.idea_id=r.idea_id WHERE i.handle=?
              ORDER BY i.added_at DESC""",(handle,)).fetchall()
            selected=[]
            for row in rows:
                d=json.loads(row['review_json'])
                if d.get('classification',{}).get('intent')=='DIRECT_SIGNAL':
                    selected.append((row['idea_id'],d))
            if not selected:
                await self.send(f'Для @{handle} немає прямих сигналів у v2.7.');return
            lines=[f'📍 @{handle} · {len(selected)} прямих сигналів',
                   'Entry = рівень із AI-аналізу тексту та графіка; порівняння — з open першої повної 5m свічки після поста (допуск 1%).',
                   'Це не доказ точного входу на зображенні чи виконаної угоди. Без ордерів.']
            for iid,d in selected:
                c=d['classification'];a=d['ai']
                comparison=await verify_tv_entry(p.prices,d)
                status=comparison['status']
                label=(f"open {comparison['candle_open']:.8g} · Δ {comparison['difference_pct']}%"
                       if status in ('NEAR_PUBLISHED_PRICE','PRICE_MISMATCH') else status)
                shown_symbol = resolve_tv_symbol(d) or 'UNKNOWN_SYMBOL'
                lines.append(f"• {iid} · {shown_symbol} {c.get('side','?')} · Entry {a.get('entry_low')} → {label}"
                             f"\n  {status} · /tvreviewidea {iid}")
            await self.send('\n'.join(lines))
        elif cmd == "/tvchartaudit":
            parts=arg.split()
            try:
                if not parts or len(parts)>2:raise ValueError('Використання: /tvchartaudit altsignals 20')
                handle,_=parse_profile(parts[0]); limit=int(parts[1]) if len(parts)>1 else 10
                if not 1<=limit<=50:raise ValueError('Ліміт 1–50')
            except ValueError as exc:
                await self.send(str(exc));return
            if self.tv_scan_task and not self.tv_scan_task.done():
                await self.send('⏳ Інший аудит уже працює');return
            self.tv_scan_task=asyncio.create_task(self._chart_audit_batch(handle,limit))
            await self.send(f'🖼 Переглядаю графіки та повні тексти @{handle}, до {limit} ідей. Без ордерів і змін Win Rate.')
        elif cmd == "/tvchartstatus":
            try: handle,_=parse_profile(arg)
            except ValueError as exc:
                await self.send(str(exc));return
            total,done,counts=tv_chart_audit.summary(self.tv_registry.db,handle)
            await self.send(f'🖼 @{handle}: {done}/{total} графіків\n'+
                '\n'.join(f'{k}: {v}' for k,v in sorted(counts.items()))+
                '\nЦе AI-кандидати, не підтверджені виконані угоди.')
        elif cmd == "/tvchartidea":
            iid=arg.strip()
            if not re.fullmatch(r'[A-Za-z0-9]{8}',iid):
                await self.send('Використання: /tvchartidea 1tjSEGWK');return
            d=tv_chart_audit.read(self.tv_registry.db,iid)
            if d is None:
                await self.send('Ще не проаналізовано. /tvchartaudit altsignals 20');return
            await self.send(f"🖼 {iid}: {d.get('verdict')} · {d.get('setup')}\n"
                f"Position Tool: {d.get('position_tool')}\n"
                f"Chart Entry/SL/TP: {d.get('chart_entry')} / {d.get('chart_stop_loss')} / {d.get('chart_targets')}\n"
                f"Text Entry/SL/TP: {d.get('text_entry')} / {d.get('text_stop_loss')} / {d.get('text_targets')}\n"
                f"Entry condition: {d.get('entry_condition')}\n"
                f"Chart evidence: {d.get('chart_evidence')}\nText evidence: {d.get('text_evidence')}\n"
                f"Uncertain: {', '.join(d.get('uncertain') or []) or '—'}\n"
                '⚠️ AI-аудит; не впливає на Win Rate, без ордерів.')
        elif cmd == "/tvcandidates":
            try:
                handle,_=parse_profile(arg.strip())
            except ValueError:
                await self.send('Використання: /tvcandidates altsignals');return
            if self.tv_scan_task and not self.tv_scan_task.done():
                await self.send('⏳ Інший процес уже працює');return
            self.tv_scan_task=asyncio.create_task(self._tv_candidates_batch(handle))
            await self.send(f'🔗 Зіставляю chart-кандидатів @{handle} зі збереженою історією та цінами (до 7 днів). Без AI, ордерів і змін Win Rate.')
        elif cmd == "/tventryaudit":
            parts=arg.split()
            try:
                if not 1<=len(parts)<=2: raise ValueError('Використання: /tventryaudit altsignals 15')
                handle,_=parse_profile(parts[0]);limit=int(parts[1]) if len(parts)==2 else 15
                if not 1<=limit<=15: raise ValueError('Ліміт 1–15, платні Gemini-запити')
            except ValueError as exc:
                await self.send(str(exc));return
            if self.tv_scan_task and not self.tv_scan_task.done():
                await self.send('⏳ Інша задача вже виконується');return
            count=len(tv_entry_audit.pending(self.tv_registry.db,handle,limit))
            if not count:
                await self.send(f'🔍 @{handle}: неперевірених кандидатів без Entry немає. /tventrystatus {handle}');return
            self.tv_scan_task=asyncio.create_task(self._focused_entry_batch(handle,limit))
            await self.send(f'🔍 Запускаю точковий аудит {count} графіків @{handle} (до {count} платних Gemini-запитів). Без ордерів та змін Win Rate.')
        elif cmd == "/tventrystatus":
            try: handle,_=parse_profile(arg.strip())
            except ValueError as exc:
                await self.send(str(exc));return
            report=await tv_entry_audit.report(self.pipeline.prices,self.tv_registry.db,handle)
            for chunk in chunks(report):await self.send(chunk)
        elif cmd == "/tventryidea":
            iid=arg.strip()
            if not re.fullmatch(r'[A-Za-z0-9]{8}',iid):
                await self.send('Використання: /tventryidea rqjD0oZ7');return
            evidence=tv_entry_audit.read(self.tv_registry.db,iid)
            if evidence is None:
                await self.send('Точковий аудит відсутній.');return
            await self.send(f"🔍 {iid}\nEntry: {evidence.get('entry')} ({evidence.get('entry_source')})\n"
                f"Доказ: {evidence.get('entry_label_or_quote')}\nSL: {evidence.get('stop_loss')} · TP: {evidence.get('targets')}\n"
                f"New: {evidence.get('is_new_call')} · Conditional: {evidence.get('conditional')} · Update: {evidence.get('update_or_result')}\n"
                f"Confidence: {evidence.get('confidence')}\n{evidence.get('explanation')}\n"
                'AI-аудит без зміни Win Rate та без ордерів.')
        elif cmd == "/tvmissed":
            parts=arg.split()
            if not 1<=len(parts)<=2:
                await self.send('Використання: /tvmissed altsignals 1');return
            try:
                handle,_=parse_profile(parts[0]);page=int(parts[1]) if len(parts)>1 else 1
                if page<1: raise ValueError('Сторінка має бути додатною')
            except ValueError as exc:
                await self.send(f'⚠️ {exc}');return
            self.tv_registry.ensure_reviews()
            items=skipped_rows(self.tv_registry.db,handle)
            pages=max(1,(len(items)+6)//7)
            if page>pages:
                await self.send(f'Сторінка {page} недоступна, всього {pages}.');return
            counts={}
            for item in items:
                k=item['triage']['priority'];counts[k]=counts.get(k,0)+1
            lines=[f'🔍 @{handle} · пропущені {len(items)} · сторінка {page}/{pages}',
                   'Евристичні кандидати, НЕ додаткові підтверджені угоди. Без Gemini та ордерів.',
                   ' · '.join(f'{k}: {v}' for k,v in sorted(counts.items()))]
            for item in items[(page-1)*7:page*7]:
                d=item['data']; c=d.get('classification') or {};a=d.get('ai') or {}
                lines.append(f"• {item['idea_id']} · {a.get('symbol') or c.get('side') or '?'} · {c.get('intent','?')}"
                    f"\n  {item['triage']['priority']} · {', '.join(item['triage']['flags'][:4]) or 'немає ознак'}"
                    f"\n  /tvmissedidea {item['idea_id']}")
            if page<pages: lines.append(f'Далі: /tvmissed {handle} {page+1}')
            await self.send('\n'.join(lines))
        elif cmd == "/tvmissedidea":
            iid=arg.strip()
            if not re.fullmatch(r'[A-Za-z0-9]{8}',iid):
                await self.send('Використання: /tvmissedidea M62VVsmv');return
            d=self.tv_registry.get_review(iid)
            if not d:
                await self.send('AI-аудит цієї ідеї відсутній.');return
            from .tv_missed import triage
            t=triage(d);c=d.get('classification') or {};a=d.get('ai') or {}
            if c.get('intent')=='DIRECT_SIGNAL':
                await self.send('Ця ідея вже DIRECT_SIGNAL. /tvreviewidea '+iid);return
            await self.send(f"🔍 {iid} · {t['original_intent']} · {c.get('side','?')}"
                f"\nПріоритет: {t['priority']} (лише евристика)"
                f"\nОзнаки: {', '.join(t['flags']) or '—'}"
                f"\nНемає рівнів: {', '.join(t['missing']) or '—'}"
                f"\nEntry: {a.get('entry_low')} · SL: {a.get('stop_loss')} · TP: {a.get('take_profits')}"
                f"\nПояснення AI: {a.get('reason','—')}"
                f"\nФрагмент джерела: {(d.get('source_excerpt') or '—')[:850]}"
                f"\nГрафік прочитано: {d.get('image_loaded',False)}"
                f"\nПосилання: {d.get('url','—')}"
                '\n⚠️ Не додано до Win Rate. Ручна перевірка оригіналу потрібна.')
        elif cmd == "/tvperformance":
            if not arg:
                await self.send('Використання: /tvperformance altsignals');return
            try: handle,_=parse_profile(arg)
            except ValueError as exc:
                await self.send(f'⚠️ {exc}');return
            if self.tv_scan_task and not self.tv_scan_task.done():
                await self.send('Зачекай, попередній процес ще працює.');return
            self.tv_scan_task=asyncio.create_task(self._tv_performance_batch(handle))
            await self.send(f'📈 Рахую історичні TP1/SL до 30 днів для @{handle}. Це може зайняти декілька хвилин. Без ордерів.')
        elif cmd == "/tvreviewidea":
            iid=arg.strip()
            if not re.fullmatch(r'[A-Za-z0-9]{8}',iid):
                await self.send('Використання: /tvreviewidea M62VVsmv');return
            d=self.tv_registry.get_review(iid)
            if d is None:
                await self.send('Другого аудиту цієї ідеї ще немає. /tvreview Altsignals 10');return
            c=d['classification'];a=d['ai']
            entry=f"{a.get('entry_low')}–{a.get('entry_high')}" if a.get('entry_low') is not None else '—'
            await self.send(f"🧾 {iid} · {c['intent']} · {c['side']}\nEntry: {entry} ({c.get('entry_mode','unknown')}; AI: {a.get('entry_type')})\nSL: {a.get('stop_loss')} · TP: {a.get('take_profits')}\nExecution: {c['execution']}\nПрапорці: {', '.join(c['flags']) or '—'}\nПричина Gemini: {a.get('reason','—')[:500]}\nРівні сценарію: {c.get('extra', {})}\nДжерело: {d['url']}\nЗображення завантажено: {d['image_loaded']}\n⚠️ Другий аудит — допоміжна класифікація, не незалежно підтверджений сигнал. Без ордерів.")
        elif cmd == "/tvaudit":
            parts=arg.split()
            if not parts or len(parts)>2:
                await self.send('Використання: /tvaudit Altsignals 1');return
            try:
                handle,_=parse_profile(parts[0]);page=int(parts[1]) if len(parts)>1 else 1
                if not 1<=page<=1000:raise ValueError('Номер сторінки має бути додатним')
            except ValueError as exc:
                await self.send(f'⚠️ {exc}');return
            total,rows=self.tv_registry.audit_page(handle,page,5)
            if not rows:
                await self.send(f'Немає ідей для @{handle}, сторінка {page}.');return
            lines=[f'🧾 @{handle} · аудит AI · сторінка {page}/{(total+4)//5} · {total} ідей',
                   'Позначки нижче прочитані із збережених звітів, не є незалежною перевіркою.']
            for row in rows:
                report=row['report'] or ''
                first=report.splitlines()[0] if report else ''
                # Show the exact model output; never infer a signal from a bullish/bearish title.
                label=first.removeprefix('🔎 ').strip() if first.startswith('🔎 ') else ('НЕ ПРОАНАЛІЗОВАНО' if not report else 'ФОРМАТ НЕ РОЗПІЗНАНО')
                title=(row['title'] or row['idea_id']).replace('\n',' ')[:100]
                lines.extend(['',f'• {row["idea_id"]} · {title}',
                              f'  {label}',
                              f'  {row["published_at"][:10] if row["published_at"] else "Дата невідома"}',
                              f'  /tvauditidea {row["idea_id"]}'])
            if page*5<total:lines.append(f'Далі: /tvaudit {handle} {page+1}')
            await self.send('\n'.join(lines))
        elif cmd == "/tvauditidea":
            iid=arg.strip()
            if not re.fullmatch(r'[A-Za-z0-9]{8}',iid):
                await self.send('Використання: /tvauditidea 1tjSEGWK');return
            row=self.tv_registry.audit_idea(iid)
            if not row:
                await self.send('Ідею не знайдено в базі.');return
            report=row['report'] or 'Ще немає AI-звіту.'
            await self.send(f'🧾 {iid} · @{row["handle"]}\n{row["title"] or ""}\n{row["url"]}\nДата: {row["published_at"] or "невідома"}\n\nЗБЕРЕЖЕНИЙ ЗВІТ:\n{report}\n\n⚠️ Оригінальне пояснення Gemini могло не зберегтися у v2.3. Для перевірки першоджерела відкрий URL; цей перегляд не викликає Gemini й не відкриває ордери.')
        elif cmd == "/tvmonths":
            if not arg:
                await self.send('Використання: /tvmonths Altsignals');return
            handle,_=parse_profile(arg)
            stats=self.tv_registry.audit_status(handle)
            rows=self.tv_registry.monthly_overview(handle)
            lines=[f'📊 @{handle} · покриття {stats["analyzed"]}/{stats["stored"]} ідей']
            for row in rows[:13]:
                lines.append(f'{row["month"]}: {row["ideas"]} ідей · AI {row["analyzed"]} · сигналів {row["signals"] or 0}')
            lines.append('Wins/Losses/Total R: лише після перевіреного history replay. Не рахуються з кількості сигналів.')
            await self.send('\n'.join(lines))
        elif cmd == "/tvscanstatus":
            await self.send(f"TradingView scan: {self.tv_scan_state}")
        elif cmd == "/tvadd":
            if not arg:
                await self.send("Використання: /tvadd https://www.tradingview.com/u/Altsignals/")
                return
            handle = self.tv_registry.add_author(arg)
            author_settings.set_enabled(p.storage.db, handle, True)
            await self.send(f"✅ @{handle}: автоматичний моніторинг увімкнено. Нові ідеї перевіряються щохвилини. Перевірка останньої години: python /app/tv_last_hour.py --notify")
        elif cmd == "/tvmonitors":
            rows = author_settings.list_authors(p.storage.db)
            await self.send("📡 TradingView моніторинг:\n" + "\n".join(
                ("🟢 " if enabled else "⚪️ ") + "@" + handle for handle, enabled in rows))
        elif cmd in ("/tvmonitor", "/tvunmonitor"):
            if not arg:
                await self.send(f"Використання: {cmd} <TradingView профіль або username>")
                return
            handle = author_settings.set_enabled(p.storage.db, arg, cmd == "/tvmonitor")
            await self.send(f"{'🟢 Увімкнено' if cmd == '/tvmonitor' else '⚪️ Вимкнено'} моніторинг @{handle}. Історія залишається в базі.")
        elif cmd == "/tvauthors":
            rows=self.tv_registry.authors()
            await self.send("📊 TradingView-автори:\n" + "\n".join(f"@{r['handle']} · {r['count']} ідей" for r in rows)
                            if rows else "Список порожній. /tvadd <профіль>")
        elif cmd == "/tvidea":
            items=arg.split()
            if len(items)!=2:
                await self.send("Використання: /tvidea Altsignals https://www.tradingview.com/chart/…")
                return
            iid,created,owner=self.tv_registry.add_idea(items[0],items[1])
            await self.send(("✅ Збережено" if created else "ℹ️ Уже є") + f" · idea {iid} · @{owner}. Аналіз: /tvanalyze {owner}")
        elif cmd == "/tvideas":
            if not arg:
                await self.send("Використання: /tvideas Altsignals")
                return
            rows=self.tv_registry.ideas(arg)
            await self.send("\n".join(f"• {r['idea_id']}: {r['url']}" for r in rows[:30]) if rows else "Для автора ще немає збережених ідей.")
        elif cmd == "/tvanalyze":
            if not arg:
                await self.send("Використання: /tvanalyze Altsignals")
                return
            rows=self.tv_registry.ideas(arg)
            if not rows:
                await self.send("Немає ідей. /tvidea <автор> <URL>")
                return
            await self.send("🔎 Аналізую останню додану ідею; без ордерів.")
            report=await analyze(p, rows[0]['url'])
            await self.send(report)
        elif cmd == "/tvstatus":
            rows=self.tv_registry.authors()
            await self.send(f"TradingView Registry: {len(rows)} авторів, {sum(r['count'] for r in rows)} ідей.\nМоніторинг профілів: OFF. Ордери: OFF для TV-команд.\nДжерело: додані вручну посилання; повнота історії невідома.")
        elif cmd == "/tv":
            if not arg:
                await self.send("Надішліть лінк на ідею: /tv https://www.tradingview.com/chart/…")
                return
            report, page = await p.inspect_idea(arg)
            await self.send(report)
            if page:
                await self.send_file("tv_page.html", page, "Сира сторінка, для налагодження")
        elif cmd == "/export":
            if not arg:
                await self.send("Надішліть лінк на канал: /export https://t.me/назва")
                return
            await self._export(arg)
        elif cmd == "/rank":
            await self.send(fmt_rank(p.storage.channels()))
        elif cmd == "/channels":
            channels = p.storage.channels()
            if not channels:
                await self.send("Каналів ще немає. Додайте: /add <лінк>")
            for c in channels:
                await self.send(fmt_channel(c), self._channel_buttons(c))
        elif cmd == "/monthstats":
            await self.send(monthly_summary(p.storage.db, arg))
        elif cmd == "/stats":
            await self.send(fmt_stats(p.storage))
        elif cmd == "/signals":
            await self.send(fmt_signals(p, p.storage.recent_signals(10)))
        elif cmd == "/status":
            on = [c.title for c in p.storage.channels(only_enabled=True)]
            await self.send(f"Режим: {p.cfg.trading_mode}\nМодель: {p.cfg.ai.provider} / {p.cfg.ai.parse_model}\n"
                            f"Слухаю каналів: {len(on)}" + ("\n• " + "\n• ".join(on) if on else ""))
        else:
            await self.send(HELP)

    async def _evaluate(self, ref, with_images: bool = False) -> None:
        p = self.pipeline
        await self.send("Оцінюю канал на історії цін…")
        await p.evaluate(ref, self.send, with_images=with_images)
        channel = await p._resolve_for_eval(ref)
        await self.send(fmt_evaluation(channel), self._channel_buttons(channel))

    async def _on_button(self, event) -> None:
        if event.sender_id != self.owner_id:
            await event.answer("Доступ закрито", alert=True)
            return
        p = self.pipeline
        m = re.fullmatch(r"(ok|no|on|off|check|profile|eval|export):(-?\d+)", event.data.decode())
        if not m:
            await event.answer()
            return
        action, value = m.group(1), int(m.group(2))
        await event.answer("Виконую…")
        try:
            if action == "ok":
                await self.send(await p.approve(value))
            elif action == "no":
                await self.send(await p.decline(value))
            elif action in ("on", "off"):
                p.set_enabled(value, action == "on")
                c = p.storage.channel(value)
                await self.send(fmt_channel(c), self._channel_buttons(c))
            elif action == "export":
                await self._export(value)
            elif action == "eval":
                await self._evaluate(value)
            elif action == "profile":
                await self.send(fmt_profile(p.storage.channel(value)))
            elif action == "check":
                c = p.storage.channel(value)
                n = p.cfg.app.check_messages
                await self.send(f"Перечитую останні {n} повідомлень «{c.title}»…")
                await self.send(fmt_check(p, c, await p.check(value, n)))
        except Exception as e:
            log.exception("Button failed")
            await self.send(f"⚠️ {e}")
