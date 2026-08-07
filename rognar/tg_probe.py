#!/usr/bin/env python3
"""
tg_probe — разведка публичных Telegram-хэндлов перед тем, как в них вступать.

Читает публичную превью-страницу t.me/<handle> (ту же, что видит любой человек
без Telegram) и говорит по каждому: живой или нет, канал это или чат, сколько
участников. Никакой авторизации, никакого API — только то, что Telegram сам
отдаёт всем в открытую.

Смысл: вступать вручную только туда, где есть жизнь, и не жечь лимиты аккаунта
на пустышки и мертвяк.

    python tg_probe.py                     # прогнать встроенный список
    python tg_probe.py handles.txt         # прогнать свой список (по хэндлу в строке)
    python tg_probe.py handles.txt out.csv # + выгрузить в CSV
"""

from __future__ import annotations

import csv
import html
import random
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# t.me отдаёт превью-страницу только «браузерам»; дефолтный UA urllib она игнорит.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Стартовый список: то, что удалось найти по теме «недвижимость Бразилия»
# (RU-сегмент + местный PT). Дополняй своими — по одному хэндлу в строке.
SEED = """
brazilia_forum
Brazil_For_You
slavianskiy_forum
investidorbrasileiro
urbeme
"""

TITLE_RE = re.compile(r'tgme_page_title[^>]*>\s*<span[^>]*>(.*?)</span>', re.S)
EXTRA_RE = re.compile(r'tgme_page_extra[^>]*>(.*?)</div>', re.S)
DESC_RE = re.compile(r'tgme_page_description[^>]*>(.*?)</div>', re.S)
COUNT_RE = re.compile(r'([\d\s ,\.]+)\s*(subscribers?|members?|подписчик\w*|участник\w*)', re.I)
ONLINE_RE = re.compile(r'([\d\s ,\.]+)\s*online', re.I)


@dataclass
class Probe:
    handle: str
    alive: bool = False
    kind: str = "—"          # канал / чат / бот / приватный
    title: str = ""
    members: int = 0
    online: int = 0
    note: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def live_pct(self) -> float:
        return 100.0 * self.online / self.members if self.members else 0.0


def clean(raw: str) -> str:
    """Снять теги и HTML-сущности, схлопнуть пробелы."""
    text = re.sub(r"<br\s*/?>", " ", raw)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace(" ", " ").strip()


def to_int(raw: str) -> int:
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else 0


def fetch(handle: str, timeout: float = 15.0) -> str | None:
    url = f"https://t.me/{handle}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return None if exc.code == 404 else None
    except Exception:
        return None


def probe(handle: str) -> Probe:
    handle = handle.strip().lstrip("@")
    # Ссылки вида t.me/joinchat/... и t.me/+abc — приватные приглашения,
    # у них нет публичной превью-страницы с составом.
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if handle.startswith(prefix):
            handle = handle[len(prefix):]
    handle = handle.split("?")[0].strip("/")

    out = Probe(handle=handle)
    if handle.startswith("+") or handle.startswith("joinchat/"):
        out.kind = "приватный"
        out.note = "инвайт-ссылка, состав не виден снаружи"
        return out

    page = fetch(handle)
    if page is None:
        out.note = "не открылась"
        return out

    title_m = TITLE_RE.search(page)
    if not title_m:
        out.note = "хэндл не найден / удалён"
        return out

    out.alive = True
    out.title = clean(title_m.group(1))

    extra = clean(EXTRA_RE.search(page).group(1)) if EXTRA_RE.search(page) else ""
    desc = clean(DESC_RE.search(page).group(1)) if DESC_RE.search(page) else ""

    count_m = COUNT_RE.search(extra)
    if count_m:
        out.members = to_int(count_m.group(1))
        word = count_m.group(2).lower()
        out.kind = "канал" if word.startswith(("subscriber", "подписчик")) else "чат"

    online_m = ONLINE_RE.search(extra)
    if online_m:
        out.online = to_int(online_m.group(1))
        out.kind = "чат"          # счётчик «online» бывает только у групп

    if not count_m and not online_m:
        out.kind = "бот" if handle.lower().endswith("bot") else "профиль"
        out.note = extra or "нет счётчика участников"

    # Грубая пометка по теме — чтобы сразу видеть, что вообще про недвижимость.
    haystack = f"{out.title} {desc}".lower()
    for tag, words in {
        "недвижимость": ("недвиж", "imóve", "imove", "apartament", "квартир", "realty", "estate", "leil"),
        "Бразилия": ("бразил", "brasil", "brazil", "rio", "рио", "paulo"),
        "переезд": ("внж", "пмж", "эмигр", "переезд", "виза", "visto"),
    }.items():
        if any(w in haystack for w in words):
            out.tags.append(tag)

    return out


def main() -> int:
    # Консоль Windows по умолчанию cp1251 и падает на галочках/кириллице.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]
    if args:
        with open(args[0], encoding="utf-8") as fh:
            handles = [ln for ln in (l.strip() for l in fh) if ln and not ln.startswith("#")]
    else:
        handles = [ln for ln in (l.strip() for l in SEED.splitlines()) if ln]

    print(f"Проверяю {len(handles)} хэндл(ов) через публичные превью t.me…\n")
    results: list[Probe] = []
    for i, handle in enumerate(handles, 1):
        res = probe(handle)
        results.append(res)
        mark = "✓" if res.alive else "✗"
        size = f"{res.members:,}".replace(",", " ") if res.members else "—"
        # Доля онлайна — куда честнее размера: 900 человек при 3 онлайн это кладбище,
        # а 500 при 76 онлайн — место, где реально разговаривают.
        online = f", {res.online} ({res.live_pct:.0f}%)" if res.online else ""
        tags = f"  [{', '.join(res.tags)}]" if res.tags else ""
        note = f"  ({res.note})" if res.note else ""
        print(f"{mark} {handle:<28} {res.kind:<10} {size:>9}{online:<12} {res.title[:38]}{tags}{note}")
        if i < len(handles):
            time.sleep(random.uniform(0.4, 0.9))   # вежливость к t.me, не долбим

    live = [r for r in results if r.alive]
    chats = [r for r in live if r.kind == "чат"]
    channels = [r for r in live if r.kind == "канал"]
    print(
        f"\nИтог: живых {len(live)}/{len(results)} — "
        f"чатов {len(chats)} (в них вступать руками), "
        f"каналов {len(channels)} (читаются без вступления)."
    )
    if chats:
        # Ранжируем по живости, а не по размеру — вступать надо туда, где говорят.
        top = sorted(chats, key=lambda r: (r.live_pct, r.members), reverse=True)[:5]
        print("Куда вступать в первую очередь: " + ", ".join(
            f"@{r.handle} ({r.members} / {r.live_pct:.0f}% онлайн)" for r in top
        ))

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["handle", "живой", "тип", "название", "участников", "online", "% онлайн", "теги", "заметка"])
            for r in sorted(results, key=lambda r: (r.live_pct, r.members), reverse=True):
                writer.writerow([
                    r.handle, "да" if r.alive else "нет", r.kind, r.title,
                    r.members, r.online, f"{r.live_pct:.1f}", ", ".join(r.tags), r.note,
                ])
        print(f"\nCSV: {args[1]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
