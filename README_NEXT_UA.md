# Signalbot — BingX execution diagnostics (09.10.2026)

Цей пакет зібраний з наданого `signalbot-current(1).zip` та містить **новий аудит виконання**, без автоматичного запуску live.

## Додано
- `signalbot/execution_audit.py`: відрізняє NEW, PARTIALLY_FILLED, FILLED; зіставляє виконану кількість з конкретним Hedge-leg.
- `bingx_execution_audit.py`: тільки GET до BingX за відомим `clientOrderId`.
- `tests/test_execution_audit.py`: тести виконання, часткового виконання і захисту від помилкових висновків.

## Розгортання (Mac у папці tg)
Розпакуйте вміст ZIP в корінь репозиторію, замінивши файли. Секрети і `/data` не чіпати.

```sh
python -m unittest discover -s tests -v
git add signalbot tests bingx_execution_audit.py README_NEXT_UA.md
git commit -m "Add read-only BingX fill audit"
git push origin main
railway up
```

Перевірка конкретного реального ордера **після його появи**, тільки за ID:
```sh
railway ssh
python /app/bingx_execution_audit.py BTC-USDT long YOUR_CLIENT_ORDER_ID
```

## Чому ще не live
- `railway_start.py` спеціально не дозволяє `TRADING_MODE=live`; **не прибирайте цей захист**.
- `Executor.open` поки позначає прийнятий біржею ордер як `executed`, не дочекавшись `FILLED`.
- Не реалізовано достовірну перевірку усіх різновидів прикріплених TP/SL та їх правильних trigger/quantity.
- Немає автоматичного відновлення стану на основі біржі; `bingx_reconcile.py` лише читає.
- Немає наскрізного зв'язку відкритих угод BingX із новими ідеями TradingView.

**Це перевірений діагностичний пакет, не production-ready live торгівля.**
