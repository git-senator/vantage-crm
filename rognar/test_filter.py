"""Проверка предфильтра: ловим запросы, отбрасываем объявления и болтовню.

    python rognar/test_filter.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import looks_like_lead

CASES = [
    # --- должны пройти: человек ищет ---
    ("Ребят, хотим с мужем взять квартиру в Рио, бюджет до 250к долларов", True),
    ("Подскажите, ищем жильё в Сан-Паулу, к кому обратиться?", True),
    ("Procuro apartamento em Copacabana, quanto custa?", True),
    ("Quero comprar um imóvel na praia, alguém indica corretor?", True),
    ("Looking to buy a condo in Miami, any agent here?", True),
    ("Думаю инвестировать в недвижимость за границей, посоветуйте риэлтора", True),
    # --- должны отсеяться: предложение, а не запрос ---
    ("Продам квартиру срочно, звоните!", False),
    ("Сдаю студию посуточно, недорого", False),
    ("Vendo apartamento, 2 quartos, ligue já", False),
    ("Luxury villa for sale, DM me", False),
    # --- шум ---
    ("да норм погода сегодня", False),
    ("Подписывайтесь на наш канал, там реклама и промокод", False),
    ("ок", False),
    # «Купить» без объекта — не сигнал: так в чатах покупают что угодно.
    # Пойманы вживую во «Флорипе», каждое стоило вызова модели.
    ("Все привет! У кого можно купить гречку?", False),
    ("Подскажите где купить симку недорого", False),
    ("Хочу купить машину, посоветуйте салон", False),
    ("Кто-нибудь покупал билеты на паром?", False),
    # А с объектом — сигнал, даже если порядок слов обратный.
    ("Квартиру в Флорипе хотим купить, подскажите риелтора", True),
    ("Присматриваем дом у моря, что посоветуете?", True),
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    failures = 0
    for text, expected in CASES:
        got = looks_like_lead(text)
        ok = got == expected
        failures += not ok
        print(f"{'✓' if ok else '✗'} {str(got):<5} (ждали {expected})  {text[:52]}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} — {'всё сходится' if not failures else 'ЕСТЬ ПРОМАХИ'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
