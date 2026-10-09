"""Conservative, no-AI public TradingView idea review. Never places orders."""
from signalbot.textparse import parse_call


def review(entry):
    data = entry.get('data') if isinstance(entry.get('data'), dict) else entry
    title = str(data.get('name') or data.get('title') or '')
    body = str(data.get('description') or '')
    symbol = data.get('symbol') or {}
    ticker = (symbol.get('short_name') or symbol.get('name') or '') if isinstance(symbol, dict) else ''
    # The actual author's text, not result updates or arbitrary price suggestions.
    text = (ticker.split(':')[-1].replace('.P', '') + '\n' + title + '\n' + body).strip()
    parsed = parse_call(text)
    if parsed is None:
        return {'status': 'NEEDS_REVIEW', 'reason': 'Текст не містить однозначного повного торгового сигналу. Перевір графік/умови автора.', 'text': text}
    if not parsed.entries:
        return {'status': 'NEEDS_REVIEW', 'reason': 'Відсутня явна числова ціна ENTRY; автоматичний вхід не дозволений.', 'text': text}
    return {'status': 'EXPLICIT_LEVELS', 'symbol': parsed.base_asset + parsed.quote_asset,
            'side': parsed.side, 'entry': parsed.entries, 'stop': parsed.stop_loss,
            'targets': parsed.take_profits, 'leverage': parsed.leverage,
            'reason': 'Явні рівні з тексту; це не перевірка ціни, свіжості чи виконуваності на BingX.', 'text': text}


def review_html(result):
    from html import escape
    if result['status'] != 'EXPLICIT_LEVELS':
        return '\n⚠️ <b>Потрібен аналіз:</b> ' + escape(result['reason'])
    entry = ', '.join(map(str, result['entry']))
    targets = ', '.join(map(str, result['targets']))
    return ('\n📋 <b>Рівні з тексту (не ордер):</b> ' + escape(result['symbol']) + ' ' + result['side'].upper() +
            '\nENTRY: ' + escape(entry) + '\nSL: ' + str(result['stop']) + '\nTP: ' + escape(targets) +
            '\n⚠️ ' + escape(result['reason']))
