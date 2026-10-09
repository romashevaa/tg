# BingX execution preparation — important

Цей пакет **НЕ вмикає live**. Railway guard у `railway_start.py` залишено: лише `TRADING_MODE=shadow`.

## Додано
- `BingX.prepare_hedge_close()` перевіряє один конкретний Hedge leg за symbol+side і точною кількістю (read-only).
- `BingX.close_hedge_exact()` надсилає MARKET closing BUY/SHORT або SELL/LONG **тільки після** перевірки біржової кількості. `reduceOnly` у Hedge Mode не передає.
- `Executor.close()` використовує scoped close для Futures, замість closeAllPositions.
- `bingx_reconcile.py` порівнює сигнали SQLite та біржові позиції/ордери **тільки читанням**. Запуск: `python /app/bingx_reconcile.py` у Railway SSH.
- Тести перевіряють позитивні й відмовні сценарії закриття без мережевих запитів.

## Обмеження до live
- Біржа агрегує позиції одного символу/напрямку; кількісна відповідність не встановлює ownership. Якщо існують сторонні угоди за тією ж парою, manual review.
- Не реалізовано достовірної перевірки біржових attached TP/SL і обробки часткового fill; `open_futures()` досі лише надсилає ордер та не підтверджує захист.
- `Executor.open()` поки трактує відповідь створення ордера як `executed` навіть для LIMIT. Це треба виправити перед live.
- `cancel_futures_orders()` усе ще видаляє всі ордери символу; **не застосовувати** в live поки не замінено адресним скасуванням.
- Немає автоматичного engine reconciliation / TradingView idea ↔ позиція / persistence recovery.
- Жодні реальні ордери та запити зміни режиму біржі тут не тестувалися.

## Встановлення
Розпакувати в локальний `tg` з резервною копією, перевірити тести:
```
python -m unittest discover -s tests -v
python -m compileall -q signalbot bingx_reconcile.py railway_start.py

git add signalbot tests bingx_reconcile.py LIVE_READINESS_UA.md
# commit і railway up тільки після проходження тестів
```

У Railway запуск `python /app/bingx_reconcile.py` безпечний: лише read-only запити.
