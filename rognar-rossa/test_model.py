"""Проверка, что модель отвечает и правильно судит о лидах.

    python rognar-rossa/test_model.py

Случаи подобраны по направлениям агентства и по границам, на которых судья
обычно ошибается: объявление против запроса, и чужая страна против нашей.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_env
from rognar import classify

CASES = [
    # --- лиды по направлениям ---
    ("Ребят, хотим с мужем взять квартиру в Рио у моря, бюджет до 250к долларов. К кому обратиться?", True),
    ("Procuro apartamento em Copacabana para investir, quanto custa?", True),
    ("Ищем дом в аренду во Флорипе на год, семья с детьми. Кто поможет?", True),
    ("Хочу купить участок под строительство недалеко от океана, реально найти?", True),
    ("Нужен юрист по сделке, покупаем апартаменты у застройщика в Балнеарио", True),
    ("Кто оформлял ВНЖ Бразилии через покупку недвижимости? Сколько по срокам?", True),

    # --- не лиды: объявление, чужая страна, болтовня ---
    ("Продам свою двушку в Астане, срочно, звоните", False),
    ("Продаю квартиру в Рио, подскажите нормальное агентство для сопровождения", True),
    ("Ищу квартиру в Москве, подскажите риелтора", False),
    ("Кто-нибудь знает, где тут поесть нормально?", False),
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    load_env()
    failures = 0
    for text, expected in CASES:
        verdict = classify(text)
        if verdict is None:
            print("✗ модель не ответила — смотри предупреждение выше")
            return 1
        got = bool(verdict.get("is_lead"))
        ok = got == expected
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} лид={got!s:<5} {int(verdict.get('score', 0)):>3}/100  "
              f"{verdict.get('direction', '—'):<14} {text[:44]}")
        time.sleep(1)      # не упираться в токены в минуту на ровном месте

    print(f"\n{len(CASES) - failures}/{len(CASES)} — "
          f"{'всё сходится' if not failures else 'ЕСТЬ ПРОМАХИ'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
