# Signalbot: TradingView review — 2026-10-09

Нові публікації доданих авторів отримують у Telegram попередню перевірку текстових ENTRY/SL/TP. Статус EXPLICIT_LEVELS означає лише що всі потрібні числові рівні явно згадані в тексті; це НЕ перевірка ринкової ціни, SL/TP на графіку, виконуваності на BingX і НЕ наказ на біржу. NEEDS_REVIEW означає відсутність однозначних текстових умов; перевіряйте графік вручну.

Разово просканувати і перевірити останню годину на Railway SSH:

    python /app/tv_last_hour.py --hours 1 --review --notify

Без повідомлень (лише у терміналі):

    python /app/tv_last_hour.py --hours 1 --review

Перевірка зберігає окремі ключі `review:<idea_id>` і не дублює вже надіслані перевірки. Публікації не передаються автоматично в `pipeline.py`, реальні ордери від цієї функції не відкриваються. Запити до Gemini не робляться. Перевіряються лише автори з увімкненим моніторингом `/tvmonitors`, а не особисті підписки акаунта TradingView.

Після розпакування в локальну папку tg виконайте:

    python -m unittest discover -s tests -q
    git add tv_auto_alerts.py tv_last_hour.py tv_signal_review.py tests/test_tv_signal_review.py REVIEW_UA.md
    git commit -m "Review new TradingView posts for explicit trade levels"
    git push origin main
    railway up

Перевірте `TRADING_MODE` в Railway Variables окремо: пакет його не змінює.
