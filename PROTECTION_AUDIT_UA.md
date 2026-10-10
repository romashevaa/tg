# Signalbot — BingX protection audit (read-only)

Це оновлення додає `/app/tv_protection_audit.py` для перевірки фактичних ордерів BingX проти SQLite `tv_live_orders`.

Встановлення на Mac в папці `tg`:

```bash
unzip -o ~/Downloads/signalbot-bingx-protection-audit.zip -d .
python -m unittest discover -s tests -q
git add -A && git commit -m "Add read-only BingX split-order protection audit" && git push origin main
railway up
```

Виконати УСЕРЕДИНІ Railway SSH:

```bash
python /app/tv_protection_audit.py
```

Повертає код 2, якщо є підозрілі або непідтверджені виконання; жодних POST/DELETE до BingX не робить. `NO_ATTEMPTS` означає, що перевіряти ще нічого, А НЕ що захист підтверджений.

Аудит перевіряє два clientOrderId, статуси та заповнені кількості, наявну позицію в правильному Hedge leg, приблизну пропорцію 80/20, кількість видимих SL/TP ордерів. **Він принципово не стверджує, що біржові SL/TP гарантовано встановлені**, тому що прикріплені умовні заявки можуть не бути представлені в `openOrders`. Для першого LIVE входу потрібно звірити на BingX вкладку TP/SL/Conditional, тригери, кількості, side та закриття TP1/TP2; до цього залишайте автоторгівлю вимкненою.

Увага: цей пакет НЕ скасовує старі LIMIT і НЕ відправляє захисні заявки, оскільки автоматичне втручання в невідомий стан біржі може зашкодити позиції. Не запускайте старий LIVE catch-up для створення тестової угоди. Для безпечного моніторингу змініть `TV_LIVE_EXECUTION=NO`, `TV_SMART_AUTO_EXECUTION=NO` перед деплоєм. 
