# BingX — наступний етап (тільки читання)

Цей пакет базується на `signalbot-updated-shadow.zip`, який працював на Railway 2026-10-09. Усі раніше встановлені можливості TradingView збережено.

## Додано
- `BingX.futures_positions(symbol=None)`: читання відкритих позицій із реальним ненульовим обсягом.
- `BingX.futures_open_orders(symbol=None)`: читання відкритих ордерів.
- `BingX.futures_order(symbol, order_id=... / client_order_id=...)`: перевірка статусу конкретного ордера.
- `bingx_audit.py`: безпечний CLI для читання балансу, позицій і відкритих ордерів без торгівлі.
- Автоматичні тести нових методів із моками API.

## Встановлення з Mac у папці tg
1. Резервна копія поточних файлів.
2. Розпакувати ZIP у **корінь** `tg`, замінюючи однойменні файли.
3. Запустити:

```
python -m compileall -q signalbot railway_start.py bingx_audit.py
python -m unittest discover -s tests -v
git add signalbot railway_start.py tv_auto_alerts.py tv_update_monitor.py tv_watch_policy.py bingx_audit.py config.toml tests
git commit -m "Add BingX read-only position and order audit"
git push origin main
railway up
```

## Перевірка на Railway

```
railway ssh
python /app/bingx_audit.py
```

Для однієї пари: `python /app/bingx_audit.py BTC-USDT`

**Не публікувати** значення API ключів, секретів, `.env` та сесії. Скрипт друкує тільки торгові дані акаунта — якщо їх також не хочете ділити, надішліть лише рядок `AUDIT_OK` та повідомлення про помилки.

## Що ще НЕ готово
- Режим `shadow` досі примусово обмежений у `railway_start.py`; це свідомо залишено без змін.
- Нові методи тільки читають біржу. Вони **не підключені до автоматичного обліку виконання ордерів**.
- Не реалізовано адресного закриття лише відповідної Hedge-позиції.
- Не підтверджено розміщення/повне покриття SL і TP при реальному відкритті та для частково виконаних LIMIT-ордерів.
- Не реалізовано автоматичного відновлення статусів після рестарту.
- TradingView idea_id не прив'язано до BingX orderId/positionId.
- API-методи протестовано з моками, не з реальним BingX у середовищі Railway.

Не перемикати `TRADING_MODE` у `live` на цьому етапі. Наступний етап після успішного read-only аудиту — mock/integration тестування схеми виконання, scoped close і захисних ордерів.
