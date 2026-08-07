"""Общая конфигурация Рогнара: секреты, список чатов, триггеры.

Секреты живут в `.env.rognar` (бот, аккаунт-слушатель) и в основном `.env`
проекта (ключ модели — переиспользуем тот же Groq, что и CRM). Оба файла
в .gitignore, в репозиторий не попадают.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSION = Path(__file__).resolve().parent / "rognar.session"
WATCHLIST = Path(__file__).resolve().parent / "watchlist.txt"


def load_env() -> None:
    """Подтянуть .env.rognar и .env в окружение, не затирая уже заданное."""
    for name in (".env.rognar", ".env"):
        path = ROOT / name
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def need(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(f"В .env.rognar не заполнено: {key}")
    return value


def watched_handles() -> list[str]:
    """Чаты и каналы под наблюдением — по одному хэндлу в строке."""
    if not WATCHLIST.exists():
        return []
    out = []
    for raw in WATCHLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line.lstrip("@"))
    return out


# --------------------------------------------------------------- триггеры
#
# Дешёвый предфильтр: он не решает, лид это или нет, а лишь отбирает
# сообщения, которые вообще стоит показывать модели. Задача — не пропустить
# лида (полнота важнее точности); мусор отсеет уже AI.

TRIGGERS = {
    "ru": [
        r"куп(ить|лю|им)", r"присматрива", r"ищ[уе]м?\s+(кварти|жиль|дом|апарт)",
        r"хочу\s+(купить|взять|приобрест)", r"приобрест", r"вложить",
        r"инвест\w*\s+в\s+недвиж", r"недвижимост", r"кварти", r"апартамент",
        r"новостройк", r"вторичк", r"ипотек", r"риелтор", r"риэлтор",
        r"посоветуйте\s+(агент|риелт|риэлт)", r"бюджет\s*[:\-–]?\s*\d",
        r"сколько\s+стоит\s+(кварти|дом|жиль)", r"вид\s+на\s+жительств.*недвиж",
    ],
    "pt": [
        r"comprar\s+(um\s+)?(apartamento|imóvel|imovel|casa)",
        r"procuro\s+(apartamento|imóvel|imovel|casa)",
        r"quero\s+comprar", r"investir\s+em\s+im[óo]ve", r"financiamento\s+imobili",
        r"corretor\s+de\s+im[óo]ve", r"quanto\s+custa\s+(um\s+)?apartamento",
    ],
    "en": [
        r"looking\s+to\s+buy", r"want\s+to\s+buy", r"buy(ing)?\s+(an?\s+)?(apartment|condo|property|house)",
        r"property\s+investment", r"invest\s+in\s+(property|real\s+estate)",
        r"real\s+estate\s+agent", r"how\s+much\s+(is|are)\s+(an?\s+)?(apartment|condo)",
    ],
}

TRIGGER_RE = re.compile(
    "|".join(p for group in TRIGGERS.values() for p in group),
    re.IGNORECASE | re.UNICODE,
)

# Явный шум: объявления самих агентств и спам. Если сообщение выглядит как
# ПРЕДЛОЖЕНИЕ, а не как ЗАПРОС, оно нам не лид.
NOISE_RE = re.compile(
    r"(продам|продаю|сдам|сдаю|аренда\s+от|наш\s+объект|звоните|"
    r"vendo|alugo|à\s+venda|for\s+sale|for\s+rent|dm\s+me|"
    r"подписывайтесь|реклама|промокод|casino|бонус)",
    re.IGNORECASE | re.UNICODE,
)


def looks_like_lead(text: str) -> bool:
    """Быстрый предфильтр перед обращением к модели."""
    if not text or len(text) < 15:
        return False
    if NOISE_RE.search(text):
        return False
    return bool(TRIGGER_RE.search(text))
