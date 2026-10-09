"""Read-only, source-aware scenario planning. No execution calls."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from math import isfinite
import json

@dataclass
class Scenario:
    setup: str
    side: str
    entry_low: float | None
    entry_high: float | None
    stop: float | None
    targets: list[float]
    status: str
    explanation: str
    source: str = "author_extracted"

    def dumps(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def loads(cls, data):
        return cls(**json.loads(data))


def plan(sig):
    """Classify author instructions; never derive unprovided price levels."""
    side = sig.side
    if side not in ("long", "short"):
        return Scenario("unknown", side, None, None, None, [], "NEEDS_DIRECTION", "Немає підтвердженого напрямку")
    values = (sig.entry_low, sig.entry_high)
    entries = [float(v) for v in values if v is not None and isfinite(float(v)) and float(v)>0]
    lo,hi = (min(entries),max(entries)) if entries else (None,None)
    stop = sig.stop_loss if sig.stop_loss is not None and isfinite(sig.stop_loss) and sig.stop_loss > 0 else None
    targets = [float(t) for t in sig.take_profits if t and isfinite(t) and t>0]
    if sig.entry_type == "breakout":
        setup = "breakout"
    elif sig.entry_type == "limit" and entries:
        setup = "limit_zone"
    elif sig.entry_type == "market" or (sig.kind == "new_signal" and entries):
        setup = "immediate"
    else:
        setup = "conditional_unspecified"
    if sig.kind != "new_signal":
        status = "WATCH_CONTEXT"
        note = "Автор не дав нової команди входу; це контекст, не готова угода."
    elif not entries and setup != "immediate":
        status = "NEEDS_LEVELS"
        note = "Немає підтвердженого авторського рівня для перевірки умови."
    elif stop is None or not targets:
        status = "INCOMPLETE"
        note = "Немає повних SL/TP; готовий ордер заборонено."
    else:
        status = "WATCH_ENTRY"
        note = "Є авторські рівні; потрібно перевірити ринок, актуальність та R:R."
    return Scenario(setup,side,lo,hi,stop,targets,status,note)


def evaluate(s, price, frame):
    """Checks observable closed-candle evidence only. Never returns trade execution readiness."""
    if price is None or not isfinite(price) or price<=0 or frame is None:
        return "NO_DATA", "Немає надійних даних про ціну та свічку"
    if s.status in ("WATCH_CONTEXT", "NEEDS_DIRECTION", "NEEDS_LEVELS", "INCOMPLETE"):
        return "WATCH", s.explanation
    if s.entry_low is None or s.entry_high is None:
        return "WATCH", "Точні умови входу невідомі"
    lo,hi=s.entry_low,s.entry_high
    close=frame['close']
    high=frame.get('last_high',close)
    low=frame.get('last_low',close)
    if s.stop is not None and ((s.side=='short' and close>=s.stop) or (s.side=='long' and close<=s.stop)):
        return "INVALIDATED", "Закриття свічки за рівнем інвалідації"
    if s.targets and ((s.side=='short' and low<=min(s.targets)) or (s.side=='long' and high>=max(s.targets))):
        return "REVIEW", "Ціль могла бути зачеплена; потрібна історична перевірка черговості"
    if s.setup=='limit_zone':
        if high>=lo and low<=hi:
            return "REVIEW", "Свічка торкнулась зони авторського Entry; перевірити стоп, ціль та актуальність"
        return "WAIT", "Очікування зони авторського Entry"
    if s.setup=='breakout':
        threshold=hi if s.side=='long' else lo
        crossed=(frame['previous_close']<=threshold<close) if s.side=='long' else (frame['previous_close']>=threshold>close)
        return ("REVIEW", "Свічка закрилась за рівнем breakout; потрібна перевірка сценарію") if crossed else ("WAIT", "Пробій ще не підтверджено закритою свічкою")
    if s.setup=='immediate':
        if lo<=price<=hi:
            return "REVIEW", "Ціна у зоні входу; перевірити час, SL/TP і R:R перед будь-яким рішенням"
        return "WAIT", "Ціна поза авторською зоною входу; не наздоганяти рух"
    return "WATCH", "Умови входу не специфіковані автором"


def describe(s):
    band = 'невідомо' if s.entry_low is None else f'{s.entry_low:g}–{s.entry_high:g}'
    return (f'🧭 SCENARIO ENGINE (тільки аналіз)\n'
            f'Тип: {s.setup} · статус: {s.status}\n'
            f'Авторський Entry: {band} · SL: {s.stop if s.stop is not None else "невідомо"} · TP: {s.targets or "невідомо"}\n'
            f'{s.explanation}\n'
            'Значення витягнуті з повідомлення/графіка, а не автоматично згенеровані з ціни. Без ордерів.')
