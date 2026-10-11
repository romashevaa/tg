# Unified TradingView LIVE routing — 2026-10-11

Зміни: `tv_live_engine.py` тепер має лише один LIVE-маршрут — `signalbot.one_entry_live.run`. Старі два окремі входи 80/20 не можуть бути випадково активовані через відсутність `TV_ONE_ENTRY_LIVE`. `railway_start.py` перевіряє актуальне One Entry підтвердження замість старого підтвердження двох входів. Якщо `TV_SINGLE_ENTRY_PREVIEW=YES`, LIVE-заявка не надсилається й повертається конкретний `CHECK`.

Щоб поточний LIVE-виконавець мав дозвіл на One Entry: `TRADING_MODE=live`, `TV_LIVE_EXECUTION=YES`, `TV_SMART_ENTRY_LIVE_GATE=YES`, `TV_ONE_ENTRY_LIVE=YES`, `TV_ONE_ENTRY_LIVE_ACK=I_ACCEPT_ONE_ENTRY_AND_TWO_TPS`, `TV_SINGLE_ENTRY_PREVIEW=NO`. `BINGX_LIVE_ACK=I_UNDERSTAND_REAL_ORDERS` лишається обов'язковим. `TV_SMART_AUTO_EXECUTION=YES` запускає автоматичний пошук кандидатів. Будь-який LIVE-вхід несе реальний ризик.

**НЕ ЗАВЕРШЕНО:** безперервне керування позиціями після входу, підтвердження прикріпленого SL з фактичного стану BingX, зняття застарілих LIMIT, автоматичне перенесення SL решти 20% у беззбиток після виконаного TP1, скасування залишкових ордерів, відновлення після рестарту. Поточний One Entry підтримує MARKET входи, може виставляти два TAKE_PROFIT_MARKET або один TP 100%, але повертає ONE_ENTRY_REVIEW, а не PROTECTED. Unit-тести не підтверджують коректність захисних ордерів на біржі. Не активуй новий маршрут без перевірки існуючих позицій/ордерів та усвідомлення цього ризику.

Перевірка без виставлення заявок: `python /app/tv_one_entry_audit.py` і `python /app/tv_protection_audit.py` в Railway SSH.
