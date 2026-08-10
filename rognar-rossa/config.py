"""Общая конфигурация Рогнара: секреты, список чатов, триггеры.

Секреты живут в `.env.rossa` (бот, аккаунт-слушатель) и в основном `.env`
проекта (ключ модели — переиспользуем тот же Groq, что и CRM). Оба файла
в .gitignore, в репозиторий не попадают.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# В контейнере сессия должна лежать на постоянном томе, иначе после пересборки
# образа Рогнар потребует вход по коду заново. На машине разработчика путь по
# умолчанию рядом с кодом — так ничего настраивать не нужно.
SESSION = Path(os.environ.get("ROGNAR_SESSION") or HERE / "rognar.session")
WATCHLIST = HERE / "watchlist.txt"
LOCK = SESSION.with_suffix(".lock")   # замок рядом с сессией: он её и стережёт


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
    """Подтянуть .env.rossa и .env в окружение, не затирая уже заданное.

    Свой файл, а не общий `.env.rognar` первого слушателя: это другой аккаунт,
    другой бот и другой получатель. Читались бы оба — второй бот на машине
    разработчика молча подхватил бы чужие ключи и слал лиды не туда.
    """
    for name in (".env.rossa", ".env"):
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
        raise SystemExit(f"В .env.rossa не заполнено: {key}")
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
#
# Этот слушатель шире первого Рогнара: агентство ведёт не только куплю-продажу
# жилья, но и аренду, землю, инвестиции, стройку, юридическое сопровождение
# сделок и ВНЖ за инвестиции. Направления перечислены ниже отдельными
# группами — так видно, что именно ловится, и правку легко внести точечно.
#
# Все группы — без захвата: скобки только `(?:…)`. Захватывающих групп в
# объединённом выражении набралось бы под сотню, а пользы от них нет —
# нам нужен факт совпадения, а не его разбор.

# --- кирпичи, из которых собраны шаблоны --------------------------------

# Намерение: человек чего-то хочет. Само по себе не значит ничего — в чате
# «купить» относится и к гречке, — поэтому всегда идёт рядом с объектом.
_WANT_RU = (
    r"куп(?:ить|лю|им|ите)|приобрет|приобрест|присматрива|подыскива|подбира|"
    r"ищ[уе]м?|интересует|интересна|нужн|нужен|хочу|хотим|хотел|планиру|"
    r"рассматрива|взять|брать|вложить|вложен|инвестир|снять|сниму|снимем|"
    r"арендова|подскаж|посовету|порекоменд"
)
_WANT_PT = (
    r"compr(?:ar|o|amos)|procur(?:o|ando|amos)|quero|queremos|busc(?:o|ando)|"
    r"interessad|preciso|gostaria|invest(?:ir|indo)|alug(?:ar|o)|arrend|"
    r"indica[çc]|recomend"
)
_WANT_EN = (
    r"looking\s+(?:to|for)|want(?:ing)?\s+to|planning\s+to|interested\s+in|"
    r"thinking\s+of|need\s+(?:a|an|to)|searching\s+for|advice\s+on"
)
_WANT = rf"{_WANT_RU}|{_WANT_PT}|{_WANT_EN}"

# Объекты. Жильё, земля и коммерция держатся порознь: от того, какой объект
# стоит рядом с глаголом, зависит, о каком из направлений идёт речь.
_HOUSING = (
    r"кварти|жиль[ёе]|жилья|дом(?:а|е|ик|ом|у|ов)?\b|апартамент|недвиж|студи|"
    r"вилл|таунхаус|пентхаус|новостройк|вторичк|коттедж|особняк|"
    r"apartamento|im[óo]ve|casa\b|casas\b|cobertura|kitnet|pousada|"
    r"apartment|condo|flat\b|house\b|housing|villa|townhouse|real\s+estate|"
    r"propert(?:y|ies)"
)
_LAND = (
    r"земл[яиюе]\b|земельн|участок|участк|надел\b|сотк[аи]|гектар|"
    r"terreno|lote\b|s[íi]tio|ch[áa]cara|"
    r"land\b|plot\b|acreage"
)
_COMMERCIAL = (
    r"коммерческ|офис|склад\w{0,3}\b|помещени|торгов\w*\s+площад|"
    r"апарт-?отел|гостиниц|"
    r"sala\s+comercial|galp[ãa]o|"
    r"office\s+space|warehouse|commercial\s+propert"
)
_OBJECT = rf"(?:{_HOUSING}|{_LAND}|{_COMMERCIAL})"


def _near(left: str, right: str, gap: int = 4) -> str:
    """«Одно рядом с другим» — в пределах нескольких слов и в любом порядке.

    Порядок слов в живой речи не фиксирован: «хотим купить квартиру» и
    «квартиру хотим купить» — одно и то же намерение, и требовать от шаблона
    только прямой порядок значит терять половину лидов.
    """
    span = rf"\w*\W+(?:\w+\W+){{0,{gap}}}?"
    return rf"(?:{left}){span}(?:{right})|(?:{right}){span}(?:{left})"


# --- направления --------------------------------------------------------

TRIGGERS = {
    # 1. Недвижимость: купля-продажа жилья и коммерции.
    "недвижимость": [
        _near(_WANT, _HOUSING),
        _near(_WANT, _COMMERCIAL),
        # Самодостаточные: вне темы недвижимости практически не встречаются.
        r"недвижимост", r"новостройк", r"вторичк", r"ипотек", r"риелтор", r"риэлтор",
        r"сколько\s+стоит\s+(?:\w+\s+){0,2}?(?:кварти|дом|жиль|апартамент)",
        r"продаж\w*\s+(?:недвижимост|кварти|дом)",
        r"financiamento\s+imobili", r"corretor\s+de\s+im[óo]ve",
        r"quanto\s+custa\s+(?:um\s+)?(?:apartamento|im[óo]vel|casa)",
        r"mortgage", r"realtor", r"real\s+estate\s+agent",
        r"how\s+much\s+(?:is|are|does)\b.{0,25}?(?:apartment|condo|propert|house)",
    ],
    # 2. Аренда: и тот, кто снимает, и тот, кто сдаёт через агентство.
    "аренда": [
        _near(r"снять|сниму|снимем|арендова|аренд[уы]|аренда|съём|найм|сдать", _HOUSING),
        _near(r"снять|сниму|снимем|арендова|аренд[уы]|аренда|сдать", _COMMERCIAL),
        r"(?:долгосрочн|краткосрочн|посуточн)\w*\s+аренд",
        r"аренд\w*\s+(?:недвижимост|жиль|кварти|дом|вилл)",
        r"арендодател", r"арендатор", r"договор\w*\s+аренды", r"сдать\s+в\s+аренду",
        r"alug(?:uel|ar)\s+(?:de\s+)?(?:apartamento|casa|im[óo]vel|temporada)",
        r"aluguel\s+(?:por\s+)?(?:temporada|longo\s+prazo)",
        r"loca[çc][ãa]o\s+de\s+im[óo]ve", r"inquilin", r"locat[áa]ri", r"locador",
        r"(?:rent|lease)\s+(?:an?\s+)?(?:apartment|condo|house|villa|propert)",
        r"long[- ]term\s+rental", r"short[- ]term\s+rental", r"landlord", r"tenant",
    ],
    # 3. Земля: участки под дом, под застройку, под вложение.
    "земля": [
        _near(_WANT, _LAND),
        r"земельн\w*\s+участ", r"участ\w*\s+земл",
        r"участ\w*\s+под\s+(?:строительств|застройк|дом|ижс|бизнес)",
        r"продаж\w*\s+(?:земл|участк)", r"инвестиц\w*\s+в\s+земл",
        r"terreno\s+(?:para|de)\s+(?:constru|invest|venda)",
        r"comprar\s+(?:um\s+)?(?:terreno|lote|s[íi]tio)",
        r"buy(?:ing)?\s+(?:a\s+)?(?:land|plot|lot)\b", r"land\s+for\s+(?:sale|development)",
    ],
    # 4. Инвестиции: доходность и вложения — но только когда речь об объекте,
    #    иначе в сеть лезет весь крипто- и биржевой трёп.
    "инвестиции": [
        _near(r"инвестир|инвестиц|вложить|вложен|доходност|окупаемост|"
              r"рентабельност|пассивн\w*\s+доход|\broi\b|"
              r"invest(?:ir|imento|ment)|rentabilidade|retorno|yield|cap\s+rate",
              rf"{_HOUSING}|{_LAND}|{_COMMERCIAL}", gap=5),
        r"инвестиционн\w*\s+(?:объект|проект|недвижимост|кварти|портфел)",
        r"доходн\w*\s+недвижимост", r"недвижимост\w*\s+для\s+инвестир",
        r"im[óo]vel\s+para\s+investir", r"valoriza[çc][ãa]o\s+imobili",
        r"rental\s+yield", r"investment\s+propert",
    ],
    # 5. Строительство: дом с нуля, подрядчик, проект.
    "строительство": [
        r"строительств\w*\s+(?:дом|вилл|объект|недвижимост|коттедж)",
        r"постро(?:ить|им|йка)\s+(?:\w+\s+){0,2}?(?:дом|вилл|коттедж)",
        r"строительн\w*\s+(?:компан|подрядчик|бригад|проект)",
        r"генподряд", r"подрядчик", r"застройщик", r"застройк",
        r"проект\w*\s+дом", r"смет\w*\s+на\s+строительств",
        r"дом\w*\s+под\s+ключ", r"реконструкц\w*\s+дом",
        r"construir\s+(?:uma\s+)?(?:casa|vila)", r"construtora", r"empreiteir",
        r"obra\s+(?:da\s+)?(?:casa|resid)", r"reforma\s+(?:de|da)\s+(?:casa|apartamento)",
        r"build(?:ing)?\s+a\s+house", r"general\s+contractor", r"turnkey\s+(?:house|villa|build)",
    ],
    # 6. Юридическое сопровождение сделки.
    "юридическое": [
        r"сделк\w*\s+купли[- ]?продаж", r"договор\w*\s+купли[- ]?продаж",
        r"юридическ\w*\s+(?:сопровожден|проверк|чистот)",
        _near(r"юридическ|юрист|адвокат|нотариус",
              rf"сделк|покупк|{_HOUSING}|{_LAND}", gap=5),
        r"оформлен\w*\s+сделк", r"проверк\w*\s+(?:недвижимост|объект|застройщик)",
        r"проверить\s+(?:юридическ\w*\s+)?чистот",
        r"contrato\s+de\s+compra\s+e\s+venda", r"escritura", r"matr[íi]cula\s+do\s+im[óo]vel",
        r"advogad\w*\s+imobili", r"cart[óo]rio\s+de\s+im[óo]ve",
        r"due\s+diligence", r"title\s+(?:deed|check|search)", r"conveyanc", r"escrow",
        r"real\s+estate\s+(?:lawyer|attorney)", r"purchase\s+agreement",
    ],
    # 7. ВНЖ и инвестиционная миграция — отдельная услуга агентства, поэтому
    #    ловится и без слова «недвижимость» рядом.
    "внж": [
        r"\bвнж\b", r"вид\s+на\s+жительств", r"резидентств",
        r"инвестиционн\w*\s+виз", r"виз\w*\s+инвестор", r"золот\w*\s+виз",
        r"гражданств\w*\s+за\s+инвестиц", r"миграционн\w*\s+юрист",
        r"visto\s+de\s+investidor", r"resid[êe]ncia\s+(?:por|via)\s+investimento",
        r"cidadania\s+por\s+investimento", r"visto\s+perman",
        r"golden\s+visa", r"investor\s+visa", r"residency\s+by\s+investment",
        r"citizenship\s+by\s+investment",
        r"permanent\s+residen\w*\b.{0,25}?(?:invest|propert)",
    ],
}

TRIGGER_RE = re.compile(
    "|".join(p for group in TRIGGERS.values() for p in group),
    re.IGNORECASE | re.UNICODE,
)

# Явный шум: объявления самих агентств, инфоцыгане и биржевой спам. Если
# сообщение выглядит как ПРЕДЛОЖЕНИЕ, а не как ЗАПРОС, оно нам не лид.
NOISE_RE = re.compile(
    r"(?:продам|продаю|прода[её]тся|сдам|сдаю|сда[её]тся|аренда\s+от|"
    r"наш\s+объект|наши\s+объект|звоните|пишите\s+в\s+л[иc]|"
    r"vendo|alugo|[àa]\s+venda|para\s+alugar|chama\s+no\s+(?:dm|zap|whats)|"
    r"for\s+sale|for\s+rent|dm\s+me|whatsapp\s+us|"
    r"подписывайтесь|реклама|промокод|розыгрыш|casino|казино|бонус|"
    r"крипт|форекс|трейдинг|бинарн|букмекер|ставк\w*\s+на\s+спорт|"
    r"пирамид|заработок\s+в\s+интернет|доход\s+от\s+\d)",
    re.IGNORECASE | re.UNICODE,
)

# Прямая просьба о помощи. Она перевешивает шум: «сдаю квартиру, посоветуйте
# агентство» — это клиент, хотя слово «сдаю» и стоит первым. Без этого
# исключения всё направление аренды со стороны собственника отсекалось бы.
ASK_RE = re.compile(
    r"(?:ищ[уе]м?|подскаж|посовету|порекоменд|помогите|нужен\s+(?:агент|риелт|"
    r"риэлт|юрист|адвокат|подрядчик|брокер)|кто\s+(?:может|знает|подскажет)|"
    r"как\s+(?:купить|оформить|получить|выбрать|проверить)|"
    r"algu[ée]m\s+(?:indica|conhece|sabe)|procuro|recomenda|"
    r"looking\s+for|any\s+(?:agent|realtor|lawyer|recommendation)|"
    r"can\s+anyone|how\s+(?:do|can)\s+i)",
    re.IGNORECASE | re.UNICODE,
)


def looks_like_lead(text: str) -> bool:
    """Быстрый предфильтр перед обращением к модели."""
    if not text or len(text) < 15:
        return False
    if NOISE_RE.search(text) and not ASK_RE.search(text):
        return False
    return bool(TRIGGER_RE.search(text))
