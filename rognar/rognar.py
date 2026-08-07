"""Рогнар — слушатель публичных Telegram-чатов.

Сидит в чатах, где аккаунт уже состоит, молча читает поток и ловит людей,
которые ищут недвижимость. Ничего никому не пишет и никуда не вступает —
единственное исходящее действие за всю его жизнь — сигнал владельцу в личку.

Путь сообщения:

    новое сообщение в чате
        → дешёвый предфильтр по словам (config.looks_like_lead)
        → модель решает: живой лид или трёп
        → дедупликация (одного человека не дёргаем дважды)
        → уведомление владельцу через бота

Запуск:  python rognar/rognar.py
Остановка: Ctrl+C
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import sys
import time
import urllib.request

from telethon import TelegramClient, events, functions
from telethon.errors import UserNotParticipantError
from telethon.tl.types import Channel, User

from config import SESSION, claim_session, load_env, looks_like_lead, need, watched_handles

log = logging.getLogger("rognar")

# Одного и того же автора не дёргаем чаще, чем раз в этот срок: человек может
# в одном чате написать пять сообщений подряд про одну и ту же квартиру.
DEDUP_WINDOW = 12 * 3600
_seen: dict[int, float] = {}

# Ниже этого модель считает разговор пустым — сигнал не шлём.
SCORE_FLOOR = 55

# Как часто отчитываться, что живой. Без этого молчание в логе двусмысленно:
# непонятно, то ли поток не идёт, то ли просто никто не писал про недвижимость.
HEARTBEAT = 300

# `any` считает вообще все входящие апдейты аккаунта, `seen` — только из
# наблюдаемых чатов. Вместе они отвечают на главный вопрос при тишине: связь
# отвалилась или просто в этих чатах никто не пишет.
_stats = {"any": 0, "seen": 0, "matched": 0, "sent": 0}

SYSTEM_PROMPT = """Ты — фильтр входящих для агентства недвижимости Rossa (Бразилия, работаем с русско-, англо- и португалоязычными клиентами).

Тебе дают сообщение из публичного чата. Реши, является ли автор ПОТЕНЦИАЛЬНЫМ ПОКУПАТЕЛЕМ недвижимости.

Считать лидом: человек ищет/хочет купить или инвестировать в жильё, спрашивает цены, просит совета к кому обратиться, называет бюджет или район.
НЕ считать лидом: агенты со своими объявлениями, продавцы, аренда на пару ночей, общие разговоры о рынке, новости, шутки, спам.

Ответь СТРОГО одним JSON-объектом, без пояснений:
{"is_lead": true|false, "score": 0-100, "intent": "покупка|инвестиции|аренда|неясно", "budget": "строка или null", "location": "строка или null", "language": "ru|en|pt", "summary": "одно предложение по-русски, что человеку нужно"}"""


# ----------------------------------------------------------------- модель


def classify(text: str) -> dict | None:
    """Спросить модель, лид ли это. None — если модель недоступна."""
    api_key = os.environ.get("AI_API_KEY", "").strip()
    base = os.environ.get("AI_API_BASE", "https://api.groq.com/openai/v1").strip()
    model = os.environ.get("AI_MODEL", "llama-3.3-70b-versatile").strip()
    if not api_key:
        return None

    payload = json.dumps({
        "model": model,
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:2000]},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw = body["choices"][0]["message"]["content"].strip()
        # Модель иногда оборачивает JSON в ```json … ``` — снимаем обёртку.
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start : end + 1]) if start >= 0 else None
    except Exception as exc:
        log.warning("модель не ответила: %s", exc)
        return None


# ------------------------------------------------------------ уведомление


def notify(text: str) -> bool:
    """Отправить сигнал владельцу через бота-почтальона."""
    token = os.environ.get("ROGNAR_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("ROGNAR_OWNER_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.error("бот не настроен: нет ROGNAR_BOT_TOKEN / ROGNAR_OWNER_CHAT_ID")
        return False

    payload = json.dumps({
        "chat_id": int(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as exc:
        log.error("не смог отправить уведомление: %s", exc)
        return False


def heat(score: int) -> str:
    if score >= 80:
        return "🔥 <b>Горячий лид</b>"
    if score >= 65:
        return "🟡 <b>Тёплый лид</b>"
    return "🔵 <b>Возможный лид</b>"


def render_alert(verdict: dict, *, author: str, mention: str, chat_title: str,
                 text: str, link: str | None) -> str:
    """Собрать то самое сообщение, которое увидит владелец."""
    score = int(verdict.get("score", 0))
    esc = html.escape

    lines = [
        f"{heat(score)} · {score}/100",
        "",
        f"👤 {esc(mention)}" + (f" ({esc(author)})" if author else ""),
        f"📍 {esc(chat_title)}",
        "",
        f"💬 <i>{esc(text[:600])}</i>",
        "",
        "🧠 <b>Разбор:</b>",
        f" • Намерение — {esc(str(verdict.get('intent') or 'неясно'))}",
    ]
    if verdict.get("budget"):
        lines.append(f" • Бюджет — {esc(str(verdict['budget']))}")
    if verdict.get("location"):
        lines.append(f" • Локация — {esc(str(verdict['location']))}")
    lines.append(f" • Язык — {esc(str(verdict.get('language') or '—')).upper()}")
    if verdict.get("summary"):
        lines += ["", f"📝 {esc(str(verdict['summary']))}"]
    if link:
        lines += ["", f"🔗 <a href=\"{link}\">Открыть сообщение</a>"]

    return "\n".join(lines)


# -------------------------------------------------------------- слушатель


async def is_member(client: TelegramClient, entity) -> bool:
    """Состоит ли аккаунт в этом чате/канале.

    Telegram шлёт события только из диалогов аккаунта, так что «хэндл
    резолвится» ещё ничего не значит. Спрашиваем напрямую про своё участие:
    список диалогов для этого ненадёжен — свежевступившие чаты попадают в
    него не сразу, и галочка успевает соврать.
    """
    if isinstance(entity, Channel):
        try:
            await client(functions.channels.GetParticipantRequest(entity, "me"))
            return True
        except UserNotParticipantError:
            return False
        except Exception:
            pass  # приватный/ограниченный — решаем по диалогам ниже

    async for dialog in client.iter_dialogs():
        if dialog.entity.id == entity.id:
            return True
    return False


def message_link(chat, message_id: int) -> str | None:
    """Прямая ссылка на сообщение — работает только в публичных чатах."""
    username = getattr(chat, "username", None)
    return f"https://t.me/{username}/{message_id}" if username else None


def describe_author(sender) -> tuple[str, str]:
    """Имя и кликабельное упоминание автора."""
    if isinstance(sender, User):
        name = " ".join(filter(None, [sender.first_name, sender.last_name])) or "без имени"
        mention = f"@{sender.username}" if sender.username else f"id{sender.id}"
        return name, mention
    return "", "неизвестно"


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)

    load_env()
    claim_session("слушатель")
    api_id = int(need("ROGNAR_API_ID"))
    api_hash = need("ROGNAR_API_HASH")

    if not SESSION.exists():
        raise SystemExit("Нет сессии. Сначала выполни:  python rognar/login.py")

    handles = watched_handles()
    if not handles:
        raise SystemExit("Пустой watchlist.txt — добавь хэндлы чатов, по одному в строке.")

    client = TelegramClient(str(SESSION), api_id, api_hash)
    await client.start()

    me = await client.get_me()
    log.info("Рогнар слушает от имени: %s", me.first_name)

    watched, not_joined, missing = [], [], []
    for handle in handles:
        try:
            entity = await client.get_entity(handle)
        except Exception as exc:
            missing.append(handle)
            log.warning("  ✗ @%-22s не открылся (%s)", handle, type(exc).__name__)
            continue

        title = getattr(entity, "title", handle)
        if await is_member(client, entity):
            watched.append(entity)
            log.info("  ✓ %s", title)
        else:
            not_joined.append(handle)
            log.warning("  ⚠ %-24s — НЕ вступил, событий отсюда не будет", f"@{handle}")

    if not watched:
        raise SystemExit(
            "Аккаунт не состоит ни в одном чате из watchlist.txt.\n"
            "Вступи в них вручную в Telegram и перезапусти."
        )
    if not_joined:
        log.warning("Вступи вручную, чтобы слушать: %s", ", ".join("@" + h for h in not_joined))

    log.info("Реально слушаю: %d из %d. (Ctrl+C — выход)", len(watched), len(handles))

    async def pulse():
        """Периодически отчитываться, что поток идёт, а не просто тихо."""
        while True:
            await asyncio.sleep(HEARTBEAT)
            log.info(
                "· жив: апдейтов всего %d, из наблюдаемых %d, зацепок %d, отправлено %d",
                _stats["any"], _stats["seen"], _stats["matched"], _stats["sent"],
            )

    asyncio.create_task(pulse())

    # Считаем весь входящий поток аккаунта — только количество, без чтения и
    # записи содержимого: это диагностика связи, а не слежка за перепиской.
    @client.on(events.NewMessage())
    async def on_any(event):
        _stats["any"] += 1

    @client.on(events.NewMessage(chats=watched))
    async def on_message(event):
        _stats["seen"] += 1
        text = (event.raw_text or "").strip()
        if not looks_like_lead(text):
            return
        _stats["matched"] += 1

        sender = await event.get_sender()
        if isinstance(sender, User) and (sender.bot or sender.is_self):
            return

        # Дедупликация: одного автора не дёргаем чаще раза в DEDUP_WINDOW.
        now = time.time()
        key = getattr(sender, "id", 0)
        if key and now - _seen.get(key, 0) < DEDUP_WINDOW:
            return

        chat = await event.get_chat()
        chat_title = getattr(chat, "title", "чат")
        log.info("совпадение в «%s» — спрашиваю модель…", chat_title)

        verdict = await asyncio.to_thread(classify, text)
        if not verdict or not verdict.get("is_lead"):
            log.info("  → модель: не лид")
            return
        score = int(verdict.get("score", 0))
        if score < SCORE_FLOOR:
            log.info("  → слабый сигнал (%d), пропускаю", score)
            return

        author, mention = describe_author(sender)
        alert = render_alert(
            verdict,
            author=author,
            mention=mention,
            chat_title=chat_title,
            text=text,
            link=message_link(chat, event.id),
        )
        if await asyncio.to_thread(notify, alert):
            _seen[key] = now
            _stats["sent"] += 1
            log.info("  → 📨 отправлено владельцу (%d/100)", score)

    await client.run_until_disconnected()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nРогнар остановлен.")
