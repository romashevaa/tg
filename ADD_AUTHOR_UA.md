# TradingView: AltSignals + coinpediamarkets

Додано другого автора TradingView `coinpediamarkets` без вимкнення `Altsignals`.

- Нові ідеї кожного автора перевіряються незалежно кожні 60 секунд.
- Перша синхронізація нового автора тихо зберігає його останні ідеї (не відправляє спам із попередніх публікацій).
- Нові публікації після першої синхронізації надсилатимуться власнику Telegram з позначенням автора.
- Стани дедуплікації зберігаються на `/data/tv_updates_monitor.sqlite`, тож рестарт їх не скидає.
- В оновленнях від ідей, відкритих через монітор, також вказується автор.
- Регулярне сканування сторінок оновлень залишено кожні 300 секунд; новий автор може збільшити кількість сканованих ідей до 50.
- Існуючий `TRADING_MODE=shadow` не змінено; це лише Telegram-сповіщення, не виконання угод.

## Встановлення

1. Розпакувати ZIP у **локальну** папку `tg` на Mac із заміною файлів.
2. Запустити:

```sh
python -m unittest discover -s tests -v && \
python -m py_compile tv_auto_alerts.py railway_start.py && \
git add tv_auto_alerts.py railway_start.py tests/test_tv_multi_author.py ADD_AUTHOR_UA.md && \
git commit -m "Monitor TradingView Coinpedia alongside AltSignals" && \
git push origin main && \
railway up
```

3. У логах Railway очікувати `TV discovery [Altsignals]` та `TV discovery [coinpediamarkets]`. На першому виклику нового автора `baseline=True`. Наступні — `baseline=False`.

Якщо API нового автора поверне невідому структуру або блокує запити, бот залишить AltSignals працювати: помилки одного автора ізольовано від інших.
