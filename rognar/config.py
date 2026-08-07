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
LOCK = Path(__file__).resolve().parent / "rognar.lock"


# --------------------------------------------------------------- монополия
#
# Файл сессии — обычная база SQLite, и второй клиент, открывший её
# параллельно, роняет первому цикл обновлений с «database is locked».
# Проверять живость по PID нельзя: на Windows os.kill(pid, 0) не опрашивает
# процесс, а убивает его. Поэтому берём настоящий файловый замок ОС — он
# снимается сам, когда процесс умирает, и устаревших замков не остаётся.

# Замок берём НЕ на нулевом байте, а далеко за текстом: msvcrt.locking
# блокирует область от текущей позиции, и запись имени владельца в неё же
# падает с PermissionError — процесс не может писать в собственный замок.
# Имя живёт в начале файла, замок — на LOCK_BYTE, области не пересекаются.
LOCK_BYTE = 4096

try:                                   # Windows
    import msvcrt

    def _take(handle) -> None:
        handle.seek(LOCK_BYTE)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

except ImportError:                    # POSIX
    import fcntl

    def _take(handle) -> None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


_lock_handle = None


def claim_session(who: str) -> None:
    """Занять сессию под себя или объяснить, кто её держит, и выйти."""
    global _lock_handle
    handle = open(LOCK, "r+", encoding="utf-8") if LOCK.exists() \
        else open(LOCK, "w+", encoding="utf-8")
    holder = handle.read(64).strip() or "другой процесс"   # читаем до захвата
    try:
        _take(handle)
    except OSError:
        handle.close()
        raise SystemExit(
            f"Сессию уже держит «{holder}» — «{who}» запустить нельзя.\n"
            "Одновременно работать они не могут: это один аккаунт и один файл\n"
            "сессии, второй клиент роняет первому поток обновлений.\n\n"
            "Останови слушателя (Ctrl+C в его окне) и повтори."
        )
    handle.seek(0)
    handle.truncate()
    handle.write(who)
    handle.flush()
    _lock_handle = handle          # держим открытым: закроется — снимется замок


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


def read_handles(path: Path) -> list[str]:
    """Хэндлы из файла: по одному в строке.

    Комментарий начинается с `#` и может стоять после хэндла — в списках
    удобно держать рядом замеры («kazakhstan_chat  # 64.8/ч»), поэтому
    хвост строки отрезаем, а не только целые строки-комментарии.

    Строку принимаем в любом виде, в каком её обычно копируют из Telegram:
    `dubai_chat`, `@dubai_chat`, `t.me/dubai_chat`, полная ссылка с https —
    заставлять человека вручную вырезать имя из ссылки незачем.
    """
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        for prefix in ("https://", "http://", "www.", "t.me/", "telegram.me/"):
            if line.lower().startswith(prefix):
                line = line[len(prefix):]
        out.append(line.split("?")[0].strip("/").lstrip("@"))
    return out


def watched_handles() -> list[str]:
    """Чаты под наблюдением слушателя."""
    return read_handles(WATCHLIST)


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
