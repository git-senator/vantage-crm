"""Разведка чатов по реальной активности — до вступления.

`tg_probe.py` смотрит публичную превью-страницу и знает только размер и
счётчик «онлайн». Этого мало: чат на тысячу человек со 157 «онлайн» может
молчать с прошлого года — люди просто держат приложение открытым.

Здесь мы заходим с другой стороны: читаем историю публичного чата аккаунтом
(членство для этого обычно не нужно) и считаем **сколько сообщений в час**
там реально пишут. Это и есть предсказатель того, будут ли лиды.

Заодно разбираемся с каналами. Канал — это вещание, покупателей в нём нет:
пишут только админы. Но у канала бывает привязанная группа обсуждений — вот
там и сидят живые люди с вопросами «а сколько стоит». Скрипт находит такую
группу и предлагает вступать в неё, а не в канал.

    python rognar/scout.py                  # проверить watchlist.txt
    python rognar/scout.py candidates.txt   # проверить свой список

ВАЖНО: слушатель держит файл сессии, поэтому его надо остановить на время
разведки (Ctrl+C), а потом запустить снова.
"""

from __future__ import annotations

import asyncio
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat

from config import SESSION, WATCHLIST, claim_session, load_env, need, read_handles

SAMPLE = 60          # сколько последних сообщений брать для замера темпа
PAUSE = 1.2          # пауза между чатами — не частим запросами к Telegram


@dataclass
class Scan:
    handle: str
    title: str = ""
    kind: str = "?"           # чат / канал
    members: int = 0
    rate: float = 0.0         # сообщений в час
    quiet_min: int = 10**9    # сколько молчит, в минутах
    linked: str | None = None  # группа обсуждений у канала
    error: str = ""

    @property
    def verdict(self) -> str:
        if self.error:
            return "✗"
        if self.kind == "канал":
            return "📢"           # вещание: лидов тут нет по определению
        if self.rate >= 10:
            return "🔥"
        if self.rate >= 2:
            return "🟢"
        if self.rate >= 0.3:
            return "🟡"
        return "💀"

    @property
    def quiet_human(self) -> str:
        m = self.quiet_min
        if m >= 10**8:
            return "—"
        if m < 120:
            return f"{m} мин"
        if m < 2880:
            return f"{m // 60} ч"
        return f"{m // 1440} дн"


async def scan(client: TelegramClient, handle: str) -> Scan:
    out = Scan(handle=handle)
    try:
        entity = await client.get_entity(handle)
    except FloodWaitError as exc:
        out.error = f"ждать {exc.seconds}с"
        return out
    except Exception as exc:
        out.error = type(exc).__name__.replace("Error", "")
        return out

    out.title = (getattr(entity, "title", handle) or handle)[:38]
    if isinstance(entity, Channel):
        out.kind = "канал" if entity.broadcast else "чат"
    elif isinstance(entity, Chat):
        out.kind = "чат"

    try:
        full = await client(functions.channels.GetFullChannelRequest(entity))
        out.members = getattr(full.full_chat, "participants_count", 0) or 0
        # У канала группа обсуждений — единственное место, где пишут читатели.
        linked_id = getattr(full.full_chat, "linked_chat_id", None)
        if linked_id:
            for chat in full.chats:
                if chat.id == linked_id:
                    out.linked = getattr(chat, "username", None) or chat.title[:28]
                    break
    except Exception:
        pass

    try:
        messages = await client.get_messages(entity, limit=SAMPLE)
    except Exception as exc:
        out.error = type(exc).__name__.replace("Error", "")
        return out

    if not messages:
        return out

    now = datetime.now(timezone.utc)
    out.quiet_min = int((now - messages[0].date).total_seconds() // 60)
    if len(messages) > 1:
        # Окно считаем ДО СЕЙЧАС, а не до последнего сообщения. Иначе чат, где
        # два месяца назад за час выплеснули 60 сообщений и с тех пор тишина,
        # получает 586/ч и уезжает на первое место. Молчание — это тоже часть
        # темпа, и в знаменателе оно должно учитываться.
        span = max((now - messages[-1].date).total_seconds() / 3600, 0.01)
        out.rate = len(messages) / span
    return out


async def main() -> int:
    load_env()
    claim_session("разведчик")
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else WATCHLIST
    handles = read_handles(path)

    client = TelegramClient(str(SESSION), int(need("ROGNAR_API_ID")), need("ROGNAR_API_HASH"))
    await client.start()
    print(f"Замеряю активность {len(handles)} чатов…\n")

    results = []
    for i, handle in enumerate(handles, 1):
        results.append(await scan(client, handle))
        if i < len(handles):
            await asyncio.sleep(PAUSE)
    await client.disconnect()

    # Сверху то, где разговор идёт прямо сейчас.
    results.sort(key=lambda s: (s.rate, -s.quiet_min), reverse=True)

    print(f"{'':<3}{'чат':<40}{'участников':>11}{'молчит':>9}{'сообщ/час':>11}")
    print("-" * 76)
    for s in results:
        if s.error:
            print(f"{s.verdict:<3}{(s.title or s.handle):<40}{s.error:>11}")
            continue
        members = f"{s.members:,}".replace(",", " ") if s.members else "—"
        print(f"{s.verdict:<3}{s.title:<40}{members:>11}{s.quiet_human:>9}{s.rate:>11.1f}")
        if s.linked:
            print(f"{'':<3}└─ обсуждения: @{s.linked} ← вступать сюда, а не в канал")

    good = [s for s in results if not s.error and s.kind == "чат" and s.rate >= 2]
    print("\n🔥 >10/ч   🟢 2-10/ч   🟡 <2/ч   💀 мёртвый   📢 канал (лидов нет)   ✗ недоступен")
    print(f"\nЖивых чатов: {len(good)} из {len(results)}.")
    if good:
        print("Стоит держать: " + ", ".join(f"@{s.handle}" for s in good[:10]))

    with open("scout.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["handle", "название", "тип", "участников", "молчит_мин", "сообщ_час", "обсуждения", "ошибка"])
        for s in results:
            w.writerow([s.handle, s.title, s.kind, s.members,
                        "" if s.quiet_min > 10**8 else s.quiet_min,
                        f"{s.rate:.2f}", s.linked or "", s.error])
    print("\nCSV: scout.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
