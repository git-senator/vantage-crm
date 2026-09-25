"""Localization catalog for the lead-scoring engine's generated prose.

The deterministic lead engine (`lead_scoring.py`) attaches a stable template
*key* (and parameters) to every signal, risk, recommendation and missing field,
never a finished sentence. `render(locale, key, params)` is the one place a key
becomes words, so a reason reads the same across the panel, the stored score and
the AI briefing — in the lead's own language.

Locales mirror the frontend: ``en`` (the fallback), ``ru`` and ``pt-BR``; a key
missing from a locale falls back to English, then to the key itself. This is the
same discipline as `growth_i18n`, whose `normalize_locale` it reuses.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.ai.growth_i18n import DEFAULT_LOCALE, normalize_locale

# fmt: off
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        # ---- source
        "lead_src_referral": "Came from a referral, the strongest channel.",
        "lead_src_telegram": "Found by the Telegram scout in a chat.",
        "lead_src_youtube": "Found by the Radar under a YouTube video.",
        "lead_src_reddit": "Found by the Reddit scout in a thread.",
        "lead_src_open_house": "Met at an open house.",
        "lead_src_realtor_com": "Came from realtor.com.",
        "lead_src_zillow": "Came from Zillow.",
        "lead_src_website": "Came from the website.",
        "lead_src_instagram": "Came from Instagram.",
        "lead_src_cold_call": "Sourced from a cold call.",
        "lead_src_other": "Source is not specified.",
        # ---- contact method
        "lead_contact_both": "Reachable by both email and phone.",
        "lead_contact_one": "Reachable by one channel.",
        "lead_contact_none": "No way to reach the lead yet.",
        # ---- budget
        "lead_budget_yes": "Budget is on record.",
        "lead_budget_no": "No budget captured yet.",
        # ---- location
        "lead_loc_known": "Preferred location is known.",
        # ---- agent temperature
        "lead_atemp_hot": "The agent marked this lead hot.",
        "lead_atemp_warm": "The agent marked this lead warm.",
        "lead_atemp_cold": "The agent marked this lead cold.",
        # ---- stage
        "lead_stage_new": "Still a new lead.",
        "lead_stage_contacted": "Has been contacted.",
        "lead_stage_qualified": "Qualified in the pipeline.",
        "lead_stage_touring": "Actively touring properties.",
        "lead_stage_unqualified": "Marked unqualified.",
        # ---- engagement
        "lead_eng_high": "Highly engaged — {n} interactions logged.",
        "lead_eng_mid": "Engaged — {n} interactions logged.",
        "lead_eng_some": "Some engagement — {n} interaction(s) logged.",
        "lead_eng_none": "No interactions logged yet.",
        # ---- recency
        "lead_rec_never": "Has not been contacted yet.",
        "lead_rec_2d": "Contacted within the last two days.",
        "lead_rec_1w": "Contacted within the last week.",
        "lead_rec_2w": "Last contact was over a week ago.",
        "lead_rec_quiet": "Going quiet — {d} days since last contact.",
        # ---- risks
        "lead_risk_going_cold_label": "Going cold",
        "lead_risk_going_cold_detail": "{d} days since last contact.",
        "lead_risk_no_contact_label": "No contact method",
        "lead_risk_no_contact_detail": "No email or phone.",
        "lead_risk_stalled_label": "Stalled",
        "lead_risk_stalled_detail": "Still 'new' after {days} days.",
        "lead_risk_unqualified_label": "Unqualified",
        "lead_risk_unqualified_detail": "Marked unqualified in the pipeline.",
        # ---- recommendations
        "lead_rec_followup_action": "Follow up now",
        "lead_rec_followup_reason": "Contact has gone quiet and the lead is cooling.",
        "lead_rec_addcontact_action": "Add an email or phone number",
        "lead_rec_addcontact_reason": "There is currently no way to reach this lead.",
        "lead_rec_firstcontact_action": "Make first contact",
        "lead_rec_firstcontact_reason": "The lead is new and reachable but not yet contacted.",
        "lead_rec_budget_action": "Capture the lead's budget",
        "lead_rec_budget_reason": "Budget is missing on an engaged lead, which blocks matching.",
        "lead_rec_showing_action": "Propose a showing",
        "lead_rec_showing_reason": "Strong buying intent and qualified — ready to tour.",
        "lead_rec_location_action": "Ask about preferred locations",
        "lead_rec_location_reason": "Knowing where they want to buy sharpens matching.",
        # ---- missing fields
        "lead_missing_email": "Email address",
        "lead_missing_phone": "Phone number",
        "lead_missing_budget": "Budget",
        "lead_missing_preferred_location": "Preferred location",
        "lead_missing_source": "Lead source",
    },
    "ru": {
        "lead_src_referral": "Пришёл по рекомендации — самый сильный канал.",
        "lead_src_telegram": "Найден слушателем в телеграм-чате.",
        "lead_src_youtube": "Найден Радаром в комментариях под роликом.",
        "lead_src_reddit": "Найден слушателем в ветке на Reddit.",
        "lead_src_open_house": "Знакомство на дне открытых дверей.",
        "lead_src_realtor_com": "Пришёл с realtor.com.",
        "lead_src_zillow": "Пришёл с Zillow.",
        "lead_src_website": "Пришёл с сайта.",
        "lead_src_instagram": "Пришёл из Instagram.",
        "lead_src_cold_call": "Получен через холодный звонок.",
        "lead_src_other": "Источник не указан.",
        "lead_contact_both": "Доступен и по email, и по телефону.",
        "lead_contact_one": "Доступен по одному каналу.",
        "lead_contact_none": "Пока нет способа связаться с лидом.",
        "lead_budget_yes": "Бюджет указан.",
        "lead_budget_no": "Бюджет пока не указан.",
        "lead_loc_known": "Предпочтительная локация известна.",
        "lead_atemp_hot": "Агент отметил лид как горячий.",
        "lead_atemp_warm": "Агент отметил лид как тёплый.",
        "lead_atemp_cold": "Агент отметил лид как холодный.",
        "lead_stage_new": "Пока новый лид.",
        "lead_stage_contacted": "С лидом связались.",
        "lead_stage_qualified": "Квалифицирован в воронке.",
        "lead_stage_touring": "Активно смотрит объекты.",
        "lead_stage_unqualified": "Отмечен как неквалифицированный.",
        "lead_eng_high": "Высокая вовлечённость — {n} взаимодействий.",
        "lead_eng_mid": "Вовлечён — {n} взаимодействий.",
        "lead_eng_some": "Есть вовлечённость — {n} взаимодействий.",
        "lead_eng_none": "Взаимодействий пока нет.",
        "lead_rec_never": "С лидом ещё не связывались.",
        "lead_rec_2d": "Связывались за последние два дня.",
        "lead_rec_1w": "Связывались на прошлой неделе.",
        "lead_rec_2w": "Последний контакт был более недели назад.",
        "lead_rec_quiet": "Затихает — {d} дней с последнего контакта.",
        "lead_risk_going_cold_label": "Остывает",
        "lead_risk_going_cold_detail": "{d} дней с последнего контакта.",
        "lead_risk_no_contact_label": "Нет способа связи",
        "lead_risk_no_contact_detail": "Нет email или телефона.",
        "lead_risk_stalled_label": "Застрял",
        "lead_risk_stalled_detail": "Всё ещё «новый» спустя {days} дней.",
        "lead_risk_unqualified_label": "Неквалифицирован",
        "lead_risk_unqualified_detail": "Отмечен в воронке как неквалифицированный.",
        "lead_rec_followup_action": "Свяжитесь сейчас",
        "lead_rec_followup_reason": "Контакт затих, и лид остывает.",
        "lead_rec_addcontact_action": "Добавьте email или телефон",
        "lead_rec_addcontact_reason": "Сейчас с этим лидом невозможно связаться.",
        "lead_rec_firstcontact_action": "Сделайте первый контакт",
        "lead_rec_firstcontact_reason": "Лид новый и доступен, но с ним ещё не связались.",
        "lead_rec_budget_action": "Уточните бюджет лида",
        "lead_rec_budget_reason": "У вовлечённого лида не указан бюджет — это мешает подбору.",
        "lead_rec_showing_action": "Предложите показ",
        "lead_rec_showing_reason": "Сильное намерение купить и квалифицирован — готов к показу.",
        "lead_rec_location_action": "Спросите о предпочтительных локациях",
        "lead_rec_location_reason": "Знание, где хотят купить, улучшает подбор.",
        "lead_missing_email": "Email",
        "lead_missing_phone": "Телефон",
        "lead_missing_budget": "Бюджет",
        "lead_missing_preferred_location": "Предпочтительная локация",
        "lead_missing_source": "Источник лида",
    },
    "pt-BR": {
        "lead_src_referral": "Veio por indicação, o canal mais forte.",
        "lead_src_telegram": "Encontrado pelo scout no Telegram.",
        "lead_src_youtube": "Encontrado pelo Radar sob um vídeo do YouTube.",
        "lead_src_reddit": "Encontrado pelo scout numa thread do Reddit.",
        "lead_src_open_house": "Conheceu em um open house.",
        "lead_src_realtor_com": "Veio do realtor.com.",
        "lead_src_zillow": "Veio do Zillow.",
        "lead_src_website": "Veio do site.",
        "lead_src_instagram": "Veio do Instagram.",
        "lead_src_cold_call": "Origem de uma ligação fria.",
        "lead_src_other": "Origem não especificada.",
        "lead_contact_both": "Acessível por e-mail e telefone.",
        "lead_contact_one": "Acessível por um canal.",
        "lead_contact_none": "Ainda sem forma de contatar o lead.",
        "lead_budget_yes": "Orçamento registrado.",
        "lead_budget_no": "Orçamento ainda não informado.",
        "lead_loc_known": "Localização preferida conhecida.",
        "lead_atemp_hot": "O agente marcou este lead como quente.",
        "lead_atemp_warm": "O agente marcou este lead como morno.",
        "lead_atemp_cold": "O agente marcou este lead como frio.",
        "lead_stage_new": "Ainda um lead novo.",
        "lead_stage_contacted": "Já foi contatado.",
        "lead_stage_qualified": "Qualificado no funil.",
        "lead_stage_touring": "Visitando imóveis ativamente.",
        "lead_stage_unqualified": "Marcado como não qualificado.",
        "lead_eng_high": "Muito engajado — {n} interações registradas.",
        "lead_eng_mid": "Engajado — {n} interações registradas.",
        "lead_eng_some": "Algum engajamento — {n} interação(ões) registrada(s).",
        "lead_eng_none": "Nenhuma interação registrada ainda.",
        "lead_rec_never": "Ainda não foi contatado.",
        "lead_rec_2d": "Contatado nos últimos dois dias.",
        "lead_rec_1w": "Contatado na última semana.",
        "lead_rec_2w": "Último contato há mais de uma semana.",
        "lead_rec_quiet": "Esfriando — {d} dias desde o último contato.",
        "lead_risk_going_cold_label": "Esfriando",
        "lead_risk_going_cold_detail": "{d} dias desde o último contato.",
        "lead_risk_no_contact_label": "Sem forma de contato",
        "lead_risk_no_contact_detail": "Sem e-mail ou telefone.",
        "lead_risk_stalled_label": "Parado",
        "lead_risk_stalled_detail": "Ainda 'novo' após {days} dias.",
        "lead_risk_unqualified_label": "Não qualificado",
        "lead_risk_unqualified_detail": "Marcado como não qualificado no funil.",
        "lead_rec_followup_action": "Faça o follow-up agora",
        "lead_rec_followup_reason": "O contato esfriou e o lead está esfriando.",
        "lead_rec_addcontact_action": "Adicione um e-mail ou telefone",
        "lead_rec_addcontact_reason": "No momento não há como contatar este lead.",
        "lead_rec_firstcontact_action": "Faça o primeiro contato",
        "lead_rec_firstcontact_reason": "O lead é novo e acessível, mas ainda não foi contatado.",
        "lead_rec_budget_action": "Registre o orçamento do lead",
        "lead_rec_budget_reason": "Falta orçamento em um lead engajado, o que bloqueia o match.",
        "lead_rec_showing_action": "Proponha uma visita",
        "lead_rec_showing_reason": "Forte intenção de compra e qualificado — pronto para visitar.",
        "lead_rec_location_action": "Pergunte sobre localizações preferidas",
        "lead_rec_location_reason": "Saber onde querem comprar melhora o match.",
        "lead_missing_email": "E-mail",
        "lead_missing_phone": "Telefone",
        "lead_missing_budget": "Orçamento",
        "lead_missing_preferred_location": "Localização preferida",
        "lead_missing_source": "Origem do lead",
    },
}
# fmt: on


def render(locale: str, key: str, params: Mapping[str, object] | None = None) -> str:
    """Localize `key` for `locale`, interpolating `params`.

    Falls back to English, then to the key itself — the never-blank contract. A
    template referencing a parameter the caller did not supply degrades to the
    raw template rather than raising.
    """
    loc = normalize_locale(locale)
    template = (
        TEMPLATES.get(loc, {}).get(key)
        or TEMPLATES[DEFAULT_LOCALE].get(key)
        or key
    )
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


__all__ = ["TEMPLATES", "render"]
