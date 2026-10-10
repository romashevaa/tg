# LIVE: відновлення таймфрейму для старих AI-аналізів

`tv_live_catchup.py` тепер відновлює відсутній `timeframe` із полів TradingView API (`interval`, `resolution`, тощо), явно написаного TF в описі ідеї або, за потреби, з розпізнавання графіка Gemini. Якщо TF достовірно не видно, він лишається невідомим і застосовується старий 20-хвилинний захист. Попередні ENTRY/SL/TP не змінюються. Підтверджений TF зберігається у `tv_full_chart_reviews`; результат спроби — у `tv_timeframe_recovery`, щоб не витрачати Gemini повторно. Тимчасові HTTP-помилки можна повторити при наступній перевірці.

Встановлення: розпакуй `tg/` поверх проєкту. Запусти `python -m unittest discover -s tests -q`, `git add . && git commit -m "Recover cached TradingView timeframes" && git push origin main`, потім `railway up`.

У Railway: `railway ssh`, потім `python /app/tv_live_catchup.py --hours 16 --notify`.

**УВАГА:** при `TRADING_MODE=live` і `TV_LIVE_EXECUTION=YES` ця команда може створити справжні ф'ючерсні ордери BingX. Це не просто аудит. Після будь-якої спроби перевірити `python /app/tv_live_audit.py` та захисні TP/SL безпосередньо на біржі. Жодних попередніх кешованих угод не треба відкривати без перевірки актуальності і підтвердження SL/TP. Навіть якщо код дає `PENDING_VERIFY`, це не гарантія, що ордер виконано або захисні ордери активні.
