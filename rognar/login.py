"""Разовый вход аккаунта-слушателя.

Запускается ОДИН раз и вручную: Telegram пришлёт код подтверждения, его надо
ввести с клавиатуры. После этого рядом ляжет файл `rognar.session` — дальше
слушатель поднимается сам, без кода.

    python rognar/login.py

Файл сессии равнозначен доступу к аккаунту: он в .gitignore и никуда не уезжает.
Повторный запуск ничего не ломает — просто покажет, кто уже вошёл.
"""

from __future__ import annotations

import asyncio
import os
import sys

from telethon import TelegramClient

from config import SESSION, load_env, need


async def main() -> int:
    load_env()
    api_id = int(need("ROGNAR_API_ID"))
    api_hash = need("ROGNAR_API_HASH")

    # Номер можно заранее положить в .env.rognar. Если его нет — спрашивать
    # будет сам Telethon, поэтому своего приглашения здесь не рисуем: два
    # вопроса подряд про одно и то же только путают.
    phone = os.environ.get("ROGNAR_PHONE", "").strip()

    print("Сейчас Telegram пришлёт код — введи его.")
    print("Если на аккаунте включён облачный пароль (2FA), спросит и его.\n")

    client = TelegramClient(str(SESSION), api_id, api_hash)
    await client.start(phone=phone or (lambda: input("Номер (+5521999999999): ").strip()))

    me = await client.get_me()
    name = " ".join(filter(None, [me.first_name, me.last_name]))
    handle = f" (@{me.username})" if me.username else ""
    await client.disconnect()

    print(f"\n✅ Вошли как: {name}{handle}")
    print(f"   Сессия сохранена: {SESSION.name}")
    print("\nТеперь можно запускать слушателя:  python rognar/rognar.py")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
