"""Поиск чатов-кандидатов по ключевым словам — на всех нужных языках.

`scout.py` отвечает на вопрос «этот чат живой?», но чаты в него надо
принести руками. Здесь мы их находим: Telegram отдаёт глобальный поиск по
публичным чатам, и по каждой стране/языку мы спрашиваем его теми словами,
которыми люди сами называют свои сообщества («русские в Канаде», «imóveis
Miami», «Алматы недвижимость»).

Найденное сразу прогоняется через замер из `scout.py`, поэтому на выходе не
«вот 300 хэндлов», а «вот те, где прямо сейчас пишут». Ни в один чат при этом
вступать не нужно.

    python rognar-rossa/find.py              # все регионы
    python rognar-rossa/find.py kz ca        # только Казахстан и Канада

Результат: found.csv (всё) + rognar/candidates_found.txt (только живые,
готово к вставке в candidates.txt).

ВАЖНО: слушатель держит файл сессии — останови его на время поиска.
"""

from __future__ import annotations

import asyncio
import csv
import sys

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel

from config import SESSION, claim_session, load_env, need
from scout import Scan, scan

SEARCH_PAUSE = 1.5      # пауза между поисковыми запросами
MAX_MEASURE = 400       # потолок на замер: дальше это часы ожидания
KEEP = 100              # сколько лучших выписать в candidates_found.txt
FOUND = "found.csv"     # накопитель: сюда пишем и отсюда же возобновляем

# Замер одного чата — это три обращения к Telegram (resolve, инфо, история),
# и на паузе 1.2с аккаунт ловит FloodWait на час где-то после 170-го чата.
# Три секунды дают пройти весь список за один заход, не разозлив сервер.
MEASURE_PAUSE = 3.0

# Запросы намеренно на языке аудитории: чат бразильских инвесторов называется
# «investimento imobiliário», а не «real estate», и по-английски не найдётся.
# Двухбуквенный ключ — это то, что можно передать аргументом в командной строке.
QUERIES: dict[str, list[str]] = {
    "ru": [  # Россия и русскоязычные инвест-сообщества
        "недвижимость чат", "инвестиции чат", "чат инвесторов",
        "инвестиции в недвижимость", "новостройки чат", "бизнес чат",
        "куплю квартиру", "риэлтор чат", "загородная недвижимость",
    ],
    "kz": [  # Казахстан
        "Казахстан чат", "Алматы чат", "Астана чат",
        "недвижимость Алматы", "недвижимость Астана", "недвижимость Казахстан",
        "бизнес Казахстан", "инвестиции Казахстан",
    ],
    "ca": [  # Канада: и диаспора, и местный рынок
        "русские в Канаде", "Торонто чат", "Ванкувер чат", "Монреаль чат",
        "иммиграция Канада", "Toronto real estate", "Vancouver real estate",
        "Canada real estate investing", "Canada property chat",
    ],
    "us": [  # США
        "русские в Америке", "русские в США", "Майами чат", "Нью-Йорк чат",
        "Лос-Анджелес чат", "Miami real estate", "New York real estate",
        "US real estate investing", "real estate investors chat",
    ],
    "br": [  # Бразилия, португальский
        "imóveis", "investimento imobiliário", "comprar apartamento",
        "investidores imóveis", "mercado imobiliário", "imóveis São Paulo",
        "imóveis Rio de Janeiro", "russos no Brasil",
    ],
    "brru": [  # Бразилия по-русски: диаспора, переезд, города
        # Наш покупатель ищет чат словами своего языка, а не словами рынка.
        # Португальские запросы выше находят бразильцев — здесь ищем наших.
        "русские в Бразилии", "русскоязычные Бразилия", "Бразилия чат",
        "переезд в Бразилию", "иммиграция Бразилия", "ВНЖ Бразилия",
        "Рио де Жанейро чат", "Сан Паулу чат", "Флорианополис чат",
        "Куритиба чат", "Балнеарио Камбориу", "Форталеза чат",
        "недвижимость Бразилия", "жизнь в Бразилии",
    ],
    "es": [  # Испаноязычная Латинская Америка
        "bienes raíces", "inversión inmobiliaria", "inmuebles chat",
        "comprar departamento", "inversores inmobiliarios",
        "inmuebles Buenos Aires", "inmuebles México", "inversiones Argentina",
    ],
}


async def search(client: TelegramClient, query: str) -> list[str]:
    """Хэндлы публичных чатов и каналов, найденных по одному запросу."""
    try:
        res = await client(functions.contacts.SearchRequest(q=query, limit=50))
    except FloodWaitError as exc:
        print(f"  ждём {exc.seconds}с (Telegram притормозил)…")
        await asyncio.sleep(exc.seconds + 1)
        return await search(client, query)
    except Exception as exc:
        print(f"  ✗ «{query}»: {type(exc).__name__}")
        return []

    out = []
    for chat in res.chats:
        # Нас интересуют только публичные: без username в чат не зайти.
        if isinstance(chat, Channel) and getattr(chat, "username", None):
            out.append(chat.username)
    return out


def load_found() -> tuple[dict[str, Scan], dict[str, str]]:
    """Прошлые замеры из found.csv — чтобы не мерить одно и то же дважды.

    Возвращает сами замеры и запросы, по которым чаты когда-то нашлись:
    второй заход ищет по другим регионам и без этого затёр бы колонку.

    Строки с ошибкой пропускаем: чат мог быть недоступен из-за флуда,
    и в следующий заход его надо попробовать снова.
    """
    scans: dict[str, Scan] = {}
    origins: dict[str, str] = {}
    try:
        fh = open(FOUND, encoding="utf-8-sig", newline="")
    except FileNotFoundError:
        return scans, origins
    with fh:
        for row in csv.DictReader(fh, delimiter=";"):
            key = row["handle"].lower()
            if row.get("нашёлся_по"):
                origins[key] = row["нашёлся_по"]
            if row.get("ошибка"):
                continue
            quiet = row.get("молчит_мин") or ""
            scans[key] = Scan(
                handle=row["handle"], title=row["название"], kind=row["тип"],
                members=int(row["участников"] or 0), rate=float(row["сообщ_час"] or 0),
                quiet_min=int(quiet) if quiet else 10**9,
                linked=row.get("обсуждения") or None,
            )
    return scans, origins


async def main() -> int:
    load_env()
    claim_session("поиск чатов")
    wanted = [a.lower() for a in sys.argv[1:]] or list(QUERIES)
    unknown = [w for w in wanted if w not in QUERIES]
    if unknown:
        raise SystemExit(f"Не знаю регион: {', '.join(unknown)}. Есть: {', '.join(QUERIES)}")

    client = TelegramClient(str(SESSION), int(need("ROGNAR_API_ID")), need("ROGNAR_API_HASH"))
    await client.start()

    # dict, а не set: порядок находок сохраняет приоритет запроса, по которому
    # чат нашёлся — а заодно видно, каким словом он был пойман.
    found: dict[str, str] = {}
    for region in wanted:
        print(f"\n=== {region.upper()} ===")
        for query in QUERIES[region]:
            hits = await search(client, query)
            fresh = [h for h in hits if h.lower() not in found]
            for h in fresh:
                found[h.lower()] = query
            print(f"  «{query}» → {len(hits)} найдено, {len(fresh)} новых")
            await asyncio.sleep(SEARCH_PAUSE)

    known, origins = load_found()
    origins.update(found)          # свежий запрос точнее старого
    handles = [h for h in found if h not in known]
    if known:
        print(f"\nВ {FOUND} уже замерено {len(known)} — их пропускаю.")
    if len(handles) > MAX_MEASURE:
        print(f"Нашлось новых {len(handles)}, замерю первые {MAX_MEASURE}.")
        handles = handles[:MAX_MEASURE]

    print(f"\nЗамеряю активность {len(handles)} чатов "
          f"(~{len(handles) * MEASURE_PAUSE / 60:.0f} мин)…")
    results: list[Scan] = list(known.values())
    for i, handle in enumerate(handles, 1):
        s = await scan(client, handle)
        # FloodWait — это стоп, а не ошибка одного чата: дальше по списку всё
        # упадёт так же, и мы лишь размажем сотню чатов в мусорные строки.
        # Сохраняем что успели и говорим, когда возвращаться.
        if s.flood:
            print(f"\n⛔ Telegram притормозил аккаунт на {s.flood // 60} мин "
                  f"(осталось непроверенных: {len(handles) - i + 1}).")
            print(f"   Запусти ту же команду после {s.flood // 60} минут — "
                  f"замеренное уже в {FOUND}, продолжит с этого места.")
            break
        results.append(s)
        if i % 25 == 0:
            print(f"  {i}/{len(handles)}…")
        await asyncio.sleep(MEASURE_PAUSE)
    await client.disconnect()

    results.sort(key=lambda s: s.rate, reverse=True)
    live = [s for s in results if not s.error and s.kind == "чат" and s.rate >= 2]

    print(f"\n{'':<3}{'чат':<40}{'участников':>11}{'молчит':>9}{'сообщ/час':>11}")
    print("-" * 76)
    for s in live[:KEEP]:
        members = f"{s.members:,}".replace(",", " ") if s.members else "—"
        print(f"{s.verdict:<3}{s.title:<40}{members:>11}{s.quiet_human:>9}{s.rate:>11.1f}")

    with open(FOUND, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["handle", "название", "тип", "участников", "сообщ_час",
                    "молчит_мин", "нашёлся_по", "обсуждения", "ошибка"])
        for s in results:
            w.writerow([s.handle, s.title, s.kind, s.members, f"{s.rate:.2f}",
                        "" if s.quiet_min > 10**8 else s.quiet_min,
                        origins.get(s.handle.lower(), ""), s.linked or "", s.error])

    out = ["# Найдено автоматически, отсортировано по сообщениям в час.",
           "# Проверь глазами 2-3 верхних, прежде чем вступать.", ""]
    out += [f"https://t.me/{s.handle}  # {s.rate:.1f}/ч, {s.members} чел. — {s.title}"
            for s in live[:KEEP]]
    (SESSION.parent / "candidates_found.txt").write_text("\n".join(out) + "\n", encoding="utf-8")

    print(f"\nЖивых чатов: {len(live)} из {len(results)} проверенных.")
    print("CSV: found.csv    Список: rognar/candidates_found.txt")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
