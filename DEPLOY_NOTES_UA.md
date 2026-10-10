# Signalbot — оновлення з ZIP

## Що зроблено
- Пошук нових AltSignals ідей кожні 60 с незалежно від перевірки оновлень старих ідей.
- Перевірка оновлень старих ідей кожні 300 с (налаштовується TV_UPDATES_SECONDS); закриті автором ідеї пропускаються.
- Для закритих ідей використовується окрема таблиця `tv_closed_ideas` у наявній постійній `/data/tv_updates_monitor.sqlite`. Історія не видаляється.
- `margin_type` автора для парсера за правилами та AI, запасний із config.toml.
- `take_profit = "middle"`; за 2 цілей це TP1.
- Перевірка напрямку та округлення SL/TP при формуванні торгового плану.
- Помилки перемикання режиму маржі BingX більше не приховуються.
- Небезпечне закриття всіх Futures-позицій одного символу в live заблоковане до реалізації адресного закриття.

## Чого НЕ зроблено
- **НЕ готово до live**: runner навмисно блокує `TRADING_MODE != shadow`.
- TradingView API-дискавері не перетворює нові ідеї на угоди автоматично: надсилає повідомлення.
- Немає перевіреного зв’язку idea_id ↔ signal_id; тому пропущені ідеї без позицій НЕ виключаються автоматично (крім завершених автором).
- Підтвердження фактичного біржового виконання LIMIT, статусів TP/SL, scoped close Hedge, restart-reconciliation ще не реалізоване.
- Для відсутнього SL/TP нічого не вигадуємо, діють попередні правила валідатора.
- Наявні `max_leverage`, `max_margin_pct`, `max_open_positions` не видалені й лишаються конфігураційними.
- Зміни не тестувалися на реальній BingX біржі.

## Встановлення з Mac (папка tg)
Зроби резервну копію локальних файлів, після чого розпакуй цей ZIP **в корінь `tg`** із заміною однойменних файлів. Папки `signalbot/` та файли `railway_start.py`, `tv_auto_alerts.py`, `tv_update_monitor.py`, `tv_watch_policy.py`, `config.toml` мають бути в корені.

Виконай:
```bash
python -m compileall -q signalbot railway_start.py tv_auto_alerts.py tv_update_monitor.py tv_watch_policy.py
python -m unittest discover -s tests -v
git add signalbot railway_start.py tv_auto_alerts.py tv_update_monitor.py tv_watch_policy.py config.toml tests
git commit -m "Separate TV polling and improve shadow signal safety"
git push origin main
railway up
```

У логах перевір:
- `TV discovery watcher started, interval=60s`
- `TV update watcher started, interval=300s`
- відсутність помилок.

Не перемикай у `live` на цій версії. API-ключі та бази з Railway лишаються на `/data`.
