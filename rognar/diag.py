"""Диагностика: доходит ли до нас трафик наблюдаемых чатов.

Отвечает на вопрос, который не разводит обычный лог: связь вообще есть, или
в чатах правда тихо. Читает последние сообщения истории и показывает, когда
они были — если история свежая, а событий нет, значит проблема в потоке
апдейтов, а не в чатах.

    python rognar/diag.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from telethon import TelegramClient

from config import SESSION, load_env, need, watched_handles


async def main() -> int:
    load_env()
    client = TelegramClient(str(SESSION), int(need("ROGNAR_API_ID")), need("ROGNAR_API_HASH"))
    await client.start()

    me = await client.get_me()
    print(f"Аккаунт: {me.first_name} (@{me.username})")
    print(f"Соединение: {'живое' if client.is_connected() else 'НЕТ'}\n")

    now = datetime.now(timezone.utc)
    rows = []

    for handle in watched_handles():
        try:
            entity = await client.get_entity(handle)
            # Берём срез последних сообщений: по разбросу их дат видно реальный
            # темп разговора. Счётчик «онлайн» этого не показывает — человек
            # может держать приложение открытым и не писать годами.
            messages = await client.get_messages(entity, limit=50)
        except Exception as exc:
            rows.append((handle[:40], None, 0.0, type(exc).__name__))
            continue

        title = (getattr(entity, "title", handle) or handle)[:40]
        if not messages:
            rows.append((title, None, 0.0, "нет истории"))
            continue

        newest, oldest = messages[0].date, messages[-1].date
        span_hours = max((newest - oldest).total_seconds() / 3600, 0.01)
        rate = len(messages) / span_hours if len(messages) > 1 else 0.0
        rows.append((title, newest, rate, ""))

    # Сортируем по свежести: сверху то, где разговор идёт прямо сейчас.
    rows.sort(key=lambda r: r[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    print(f"{'чат':<42} {'молчит':>12} {'сообщений/час':>15}")
    print("-" * 72)
    for title, newest, rate, err in rows:
        if err:
            print(f"{title:<42} {err:>12}")
            continue
        mins = int((now - newest).total_seconds() // 60)
        quiet = f"{mins} мин" if mins < 120 else (f"{mins // 60} ч" if mins < 2880 else f"{mins // 1440} дн")
        verdict = "🔥" if rate >= 10 else "🟢" if rate >= 2 else "🟡" if rate >= 0.3 else "💀"
        print(f"{title:<42} {quiet:>12} {rate:>14.1f} {verdict}")

    print("\n🔥 >10/ч — поток   🟢 2-10/ч — живо   🟡 <2/ч — вяло   💀 мёртвый")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
