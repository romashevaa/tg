# Smart Auto LIVE — автоматичне відновлення таймфрейму

Фоновий `tv_smart_scan.py` тепер перевіряє відсутній TF у кеші, використовуючи наявний `tv_timeframe_recovery.recover`: метадані, текст TradingView, а за потреби явно позначений TF на графіку через Gemini. Відновлені значення кешуються у SQLite та не змінюють ENTRY/SL/TP.

Після запуску перевіряйте в Railway Deploy Logs рядки `TV Smart Entry watcher started`, `TV Smart Entry cycle started/finished` та `SMART_TF_RECOVERY`. Команда `python /app/tv_smart_scan.py --hours 16` залишається read-only. Режим LIVE виконує тільки фоновий watcher за наявності відповідних налаштувань.

Немає гарантії, що Gemini зможе визначити TF: без достовірних даних статус лишається CHECK, угода не відкривається. Відкриття реальної угоди потребує решти ринкових і біржових перевірок. Реальне виконання двох ордерів та захисних TP/SL не підтверджене біржовим тестом.
