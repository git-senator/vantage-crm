"""Разовый вход аккаунта-слушателя.

Запускается ОДИН раз и вручную: Telegram пришлёт код подтверждения, его надо
ввести с клавиатуры. После этого рядом ляжет файл `rognar.session` — дальше
слушатель поднимается сам, без кода.

    python rognar/login.py

Файл сессии равнозначен доступу к аккаунту: он в .gitignore и никуда не уезжает.
"""

from __future__ import annotations

import os
import sys

from telethon import TelegramClient

from config import SESSION, load_env, need


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    load_env()
    api_id = int(need("ROGNAR_API_ID"))
    api_hash = need("ROGNAR_API_HASH")

    # Номер можно заранее положить в .env.rognar, иначе спросим здесь.
    phone = os.environ.get("ROGNAR_PHONE", "").strip()
    if not phone:
        phone = input("Номер телефона (в формате +79991234567): ").strip()

    print("\nСейчас Telegram пришлёт код — введи его сюда.")
    print("Если на аккаунте включён облачный пароль (2FA), спросит и его.\n")

    with TelegramClient(str(SESSION), api_id, api_hash) as client:
        client.start(phone=lambda: phone)
        me = client.get_me()
        name = " ".join(filter(None, [me.first_name, me.last_name]))
        handle = f" (@{me.username})" if me.username else ""
        print(f"\n✅ Вошли как: {name}{handle}")
        print(f"   Сессия сохранена: {SESSION.name}")
        print("\nТеперь можно запускать слушателя:  python rognar/rognar.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
