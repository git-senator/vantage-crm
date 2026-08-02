"""Localization catalog for the growth-intelligence engine's generated prose.

The deterministic growth engine (`growth_scoring.py`) reasons in stable template
*keys* with parameters — never in a finished sentence — so the one place a
number becomes words is here. `render(locale, key, params)` turns a key into a
localized string; the engine renders its own English (the fallback and the text
the AI briefing and the stored breakdown read) through the very same table, so
there is a single source of truth for every reason across all languages.

Locales mirror the frontend: ``en`` (the fallback), ``ru`` and ``pt-BR``. A key
missing from a locale falls back to English, then to the key itself, which is
the same never-blank contract the UI dictionaries follow.
"""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_LOCALE = "en"

#: Every generated reason template, keyed. Parameters are pre-formatted strings
#: (percentages, day counts, money) so a template only interpolates — the engine
#: owns rounding, the catalog owns wording.
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        # --- signals: lead conversion
        "conversion_none": "No leads created this period to convert.",
        "conversion_strong": "Strong lead conversion at {r}%.",
        "conversion_healthy": "Healthy lead conversion at {r}%.",
        "conversion_moderate": "Moderate lead conversion at {r}%.",
        "conversion_low": "Low lead conversion at {r}%.",
        "conversion_zero": "No leads converted this period.",
        # --- signals: win rate
        "win_none": "No deals closed this period.",
        "win_strong": "Strong win rate at {r}%.",
        "win_healthy": "Healthy win rate at {r}%.",
        "win_moderate": "Moderate win rate at {r}%.",
        "win_low": "Low win rate at {r}%.",
        # --- signals: revenue trend
        "rev_booked": "Revenue booked this period.",
        "rev_none": "No revenue booked this period.",
        "rev_up": "Revenue up {d}% on the previous period.",
        "rev_flat": "Revenue flat on the previous period.",
        "rev_down": "Revenue down {d}% on the previous period.",
        # --- signals: pipeline coverage
        "pipe_building": "Pipeline is building.",
        "pipe_none": "No weighted pipeline to carry future revenue.",
        "pipe_covers": "Weighted pipeline covers {x}x last period's revenue.",
        "pipe_thin": "Thin pipeline — only {x}x last period's revenue.",
        # --- signals: sales cycle
        "cycle_none": "No completed sales cycle to measure.",
        "cycle_fast": "Fast sales cycle at {d} days.",
        "cycle_reasonable": "Reasonable sales cycle at {d} days.",
        "cycle_slow": "Slow sales cycle at {d} days.",
        "cycle_long": "Long sales cycle at {d} days.",
        # --- signals: activity
        "act_none": "No activity logged this period.",
        "act_logged": "{n} activities logged.",
        "act_up": "Activity up {d}% — {n} logged.",
        "act_steady": "Activity down {d}% but steady.",
        "act_down": "Activity down {d}% on the previous period.",
        # --- signals: deal flow
        "flow_no_new": "No new deals opened this period.",
        "flow_none": "No deal activity this period.",
        "flow_opened": "{n} new deals opened.",
        "flow_replenishing": "{n} deals opened against {c} closed — replenishing.",
        "flow_opened_vs": "{n} deals opened against {c} closed.",
        # --- signals: task hygiene
        "task_none": "No overdue tasks.",
        "task_few": "{n} overdue task(s).",
        "task_piling": "{n} overdue tasks are piling up.",
        "task_slipping": "{n} overdue tasks — follow-up is slipping.",
        # --- revenue signals
        "rs_revenue_up": "Revenue {rev} won {period}, up {pct}% on the previous period.",
        "rs_revenue_down": "Revenue {rev} won {period}, down {pct}% on the previous period.",
        "rs_revenue_flat": "Revenue {rev} won {period}.",
        "rs_commission": "Commission earned {c} {period}.",
        "rs_avg": "Average deal size {v}.",
        "rs_coverage": "Weighted pipeline {v} covers {x}x last period's revenue.",
        "rs_stands": "Weighted pipeline stands at {v}.",
        # --- pipeline insights
        "pi_heaviest": "{stage} holds the most open value at {value} across {n} deal(s).",
        "pi_slowest": "{stage} is the slowest stage at {days} days on average.",
        "pi_slowest_bottleneck": (
            "{stage} is the slowest stage at {days} days on average — the likely bottleneck."
        ),
        # --- risks (label + detail)
        "risk_revenue_declining_label": "Revenue declining",
        "risk_revenue_declining_detail": "Down {pct}% on the previous period.",
        "risk_low_win_rate_label": "Low win rate",
        "risk_low_win_rate_detail": "Only {pct}% of closed deals won.",
        "risk_thin_pipeline_label": "Thin pipeline",
        "risk_thin_pipeline_detail": "Weighted pipeline is light against recent revenue.",
        "risk_long_sales_cycle_label": "Long sales cycle",
        "risk_long_sales_cycle_detail": "Deals take {days} days on average to win.",
        "risk_overdue_tasks_label": "Overdue tasks piling up",
        "risk_overdue_tasks_detail": "{n} tasks are past due.",
        "risk_activity_falling_label": "Activity falling",
        "risk_activity_falling_detail": "Logged activity down {pct}%.",
        "risk_weak_conversion_label": "Weak lead conversion",
        "risk_weak_conversion_detail": "Only {pct}% of new leads converted.",
        # --- recommendations (action + reason)
        "rec_thin_pipeline_action": "Build pipeline",
        "rec_thin_pipeline_reason": (
            "Weighted pipeline is light against recent revenue; open and qualify "
            "more deals to protect next period."
        ),
        "rec_revenue_declining_action": "Focus on closing",
        "rec_revenue_declining_reason": (
            "Revenue fell on the previous period; prioritise deals near their close date."
        ),
        "rec_low_win_rate_action": "Review qualification and losses",
        "rec_low_win_rate_reason": (
            "A low win rate points to weak qualification or a repeated loss reason "
            "worth investigating."
        ),
        "rec_long_sales_cycle_action": "Unblock the slowest stage",
        "rec_long_sales_cycle_reason": (
            "Deals are spending a long time in the pipeline; target the bottleneck stage."
        ),
        "rec_weak_conversion_action": "Improve lead follow-up",
        "rec_weak_conversion_reason": (
            "Few new leads are converting; tighten first-contact speed and nurture."
        ),
        "rec_overdue_tasks_action": "Clear the overdue backlog",
        "rec_overdue_tasks_reason": (
            "A growing overdue-task pile means follow-up is slipping."
        ),
        "rec_activity_falling_action": "Lift outreach",
        "rec_activity_falling_reason": (
            "Logged activity is falling, which leads pipeline down with it."
        ),
    },
    "ru": {
        "conversion_none": "За период не создано лидов для конверсии.",
        "conversion_strong": "Высокая конверсия лидов — {r}%.",
        "conversion_healthy": "Хорошая конверсия лидов — {r}%.",
        "conversion_moderate": "Умеренная конверсия лидов — {r}%.",
        "conversion_low": "Низкая конверсия лидов — {r}%.",
        "conversion_zero": "За период не сконвертировано ни одного лида.",
        "win_none": "За период не закрыто ни одной сделки.",
        "win_strong": "Высокая доля побед — {r}%.",
        "win_healthy": "Хорошая доля побед — {r}%.",
        "win_moderate": "Умеренная доля побед — {r}%.",
        "win_low": "Низкая доля побед — {r}%.",
        "rev_booked": "За период получена выручка.",
        "rev_none": "За период выручки нет.",
        "rev_up": "Выручка выросла на {d}% к прошлому периоду.",
        "rev_flat": "Выручка на уровне прошлого периода.",
        "rev_down": "Выручка упала на {d}% к прошлому периоду.",
        "pipe_building": "Воронка наполняется.",
        "pipe_none": "Нет взвешенной воронки для будущей выручки.",
        "pipe_covers": "Взвешенная воронка покрывает выручку прошлого периода в {x}×.",
        "pipe_thin": "Тонкая воронка — лишь {x}× выручки прошлого периода.",
        "cycle_none": "Нет завершённого цикла сделки для оценки.",
        "cycle_fast": "Быстрый цикл сделки — {d} дн.",
        "cycle_reasonable": "Приемлемый цикл сделки — {d} дн.",
        "cycle_slow": "Медленный цикл сделки — {d} дн.",
        "cycle_long": "Длинный цикл сделки — {d} дн.",
        "act_none": "За период не записано активностей.",
        "act_logged": "Записано активностей: {n}.",
        "act_up": "Активность выросла на {d}% — записано {n}.",
        "act_steady": "Активность снизилась на {d}%, но стабильна.",
        "act_down": "Активность упала на {d}% к прошлому периоду.",
        "flow_no_new": "За период не открыто новых сделок.",
        "flow_none": "За период нет активности по сделкам.",
        "flow_opened": "Открыто новых сделок: {n}.",
        "flow_replenishing": "Открыто {n} сделок против {c} закрытых — воронка пополняется.",
        "flow_opened_vs": "Открыто {n} сделок против {c} закрытых.",
        "task_none": "Просроченных задач нет.",
        "task_few": "Просроченных задач: {n}.",
        "task_piling": "Просроченные задачи копятся: {n}.",
        "task_slipping": "{n} просроченных задач — работа с клиентами буксует.",
        "rs_revenue_up": "Выручка {rev} за {period}, рост на {pct}% к прошлому периоду.",
        "rs_revenue_down": "Выручка {rev} за {period}, спад на {pct}% к прошлому периоду.",
        "rs_revenue_flat": "Выручка {rev} за {period}.",
        "rs_commission": "Комиссия {c} за {period}.",
        "rs_avg": "Средний размер сделки {v}.",
        "rs_coverage": "Взвешенная воронка {v} покрывает выручку прошлого периода в {x}×.",
        "rs_stands": "Взвешенная воронка составляет {v}.",
        "pi_heaviest": "Больше всего открытой стоимости на этапе «{stage}» — {value} по {n} сделке(ам).",
        "pi_slowest": "Самый медленный этап — «{stage}», в среднем {days} дн.",
        "pi_slowest_bottleneck": (
            "Самый медленный этап — «{stage}», в среднем {days} дн. — вероятное узкое место."
        ),
        "risk_revenue_declining_label": "Выручка снижается",
        "risk_revenue_declining_detail": "Спад на {pct}% к прошлому периоду.",
        "risk_low_win_rate_label": "Низкая доля побед",
        "risk_low_win_rate_detail": "Выиграно лишь {pct}% закрытых сделок.",
        "risk_thin_pipeline_label": "Тонкая воронка",
        "risk_thin_pipeline_detail": "Взвешенная воронка мала относительно недавней выручки.",
        "risk_long_sales_cycle_label": "Длинный цикл сделки",
        "risk_long_sales_cycle_detail": "На победу в среднем уходит {days} дн.",
        "risk_overdue_tasks_label": "Копятся просроченные задачи",
        "risk_overdue_tasks_detail": "Просрочено задач: {n}.",
        "risk_activity_falling_label": "Активность падает",
        "risk_activity_falling_detail": "Записанная активность снизилась на {pct}%.",
        "risk_weak_conversion_label": "Слабая конверсия лидов",
        "risk_weak_conversion_detail": "Сконвертировано лишь {pct}% новых лидов.",
        "rec_thin_pipeline_action": "Наполняйте воронку",
        "rec_thin_pipeline_reason": (
            "Взвешенная воронка мала относительно недавней выручки; открывайте и "
            "квалифицируйте больше сделок, чтобы защитить следующий период."
        ),
        "rec_revenue_declining_action": "Сфокусируйтесь на закрытии",
        "rec_revenue_declining_reason": (
            "Выручка упала к прошлому периоду; отдайте приоритет сделкам у даты закрытия."
        ),
        "rec_low_win_rate_action": "Пересмотрите квалификацию и потери",
        "rec_low_win_rate_reason": (
            "Низкая доля побед указывает на слабую квалификацию или повторяющуюся "
            "причину потерь — стоит разобраться."
        ),
        "rec_long_sales_cycle_action": "Разблокируйте самый медленный этап",
        "rec_long_sales_cycle_reason": (
            "Сделки подолгу задерживаются в воронке; займитесь узким этапом."
        ),
        "rec_weak_conversion_action": "Улучшите работу с лидами",
        "rec_weak_conversion_reason": (
            "Мало новых лидов конвертируется; ускорьте первый контакт и прогрев."
        ),
        "rec_overdue_tasks_action": "Разберите просроченные задачи",
        "rec_overdue_tasks_reason": (
            "Растущая гора просроченных задач означает, что работа с клиентами буксует."
        ),
        "rec_activity_falling_action": "Усильте охват",
        "rec_activity_falling_reason": (
            "Записанная активность падает, а вместе с ней и воронка."
        ),
    },
    "pt-BR": {
        "conversion_none": "Nenhum lead criado neste período para converter.",
        "conversion_strong": "Conversão de leads forte, {r}%.",
        "conversion_healthy": "Conversão de leads saudável, {r}%.",
        "conversion_moderate": "Conversão de leads moderada, {r}%.",
        "conversion_low": "Conversão de leads baixa, {r}%.",
        "conversion_zero": "Nenhum lead convertido neste período.",
        "win_none": "Nenhuma negociação fechada neste período.",
        "win_strong": "Taxa de vitória forte, {r}%.",
        "win_healthy": "Taxa de vitória saudável, {r}%.",
        "win_moderate": "Taxa de vitória moderada, {r}%.",
        "win_low": "Taxa de vitória baixa, {r}%.",
        "rev_booked": "Receita registrada neste período.",
        "rev_none": "Nenhuma receita registrada neste período.",
        "rev_up": "Receita subiu {d}% em relação ao período anterior.",
        "rev_flat": "Receita estável em relação ao período anterior.",
        "rev_down": "Receita caiu {d}% em relação ao período anterior.",
        "pipe_building": "O funil está em formação.",
        "pipe_none": "Sem funil ponderado para sustentar a receita futura.",
        "pipe_covers": "O funil ponderado cobre {x}× a receita do período anterior.",
        "pipe_thin": "Funil estreito — apenas {x}× a receita do período anterior.",
        "cycle_none": "Nenhum ciclo de vendas concluído para medir.",
        "cycle_fast": "Ciclo de vendas rápido, {d} dias.",
        "cycle_reasonable": "Ciclo de vendas razoável, {d} dias.",
        "cycle_slow": "Ciclo de vendas lento, {d} dias.",
        "cycle_long": "Ciclo de vendas longo, {d} dias.",
        "act_none": "Nenhuma atividade registrada neste período.",
        "act_logged": "{n} atividades registradas.",
        "act_up": "Atividade subiu {d}% — {n} registradas.",
        "act_steady": "Atividade caiu {d}%, mas segue estável.",
        "act_down": "Atividade caiu {d}% em relação ao período anterior.",
        "flow_no_new": "Nenhuma nova negociação aberta neste período.",
        "flow_none": "Nenhuma atividade de negociações neste período.",
        "flow_opened": "{n} novas negociações abertas.",
        "flow_replenishing": "{n} negociações abertas contra {c} fechadas — repondo o funil.",
        "flow_opened_vs": "{n} negociações abertas contra {c} fechadas.",
        "task_none": "Nenhuma tarefa atrasada.",
        "task_few": "{n} tarefa(s) atrasada(s).",
        "task_piling": "{n} tarefas atrasadas se acumulando.",
        "task_slipping": "{n} tarefas atrasadas — o acompanhamento está falhando.",
        "rs_revenue_up": "Receita de {rev} ({period}), alta de {pct}% sobre o período anterior.",
        "rs_revenue_down": "Receita de {rev} ({period}), queda de {pct}% sobre o período anterior.",
        "rs_revenue_flat": "Receita de {rev} ({period}).",
        "rs_commission": "Comissão de {c} ({period}).",
        "rs_avg": "Ticket médio {v}.",
        "rs_coverage": "Funil ponderado {v} cobre {x}× a receita do período anterior.",
        "rs_stands": "O funil ponderado está em {v}.",
        "pi_heaviest": "{stage} concentra o maior valor em aberto: {value} em {n} negociação(ões).",
        "pi_slowest": "{stage} é o estágio mais lento, {days} dias em média.",
        "pi_slowest_bottleneck": (
            "{stage} é o estágio mais lento, {days} dias em média — o provável gargalo."
        ),
        "risk_revenue_declining_label": "Receita em queda",
        "risk_revenue_declining_detail": "Queda de {pct}% sobre o período anterior.",
        "risk_low_win_rate_label": "Taxa de vitória baixa",
        "risk_low_win_rate_detail": "Apenas {pct}% das negociações fechadas foram ganhas.",
        "risk_thin_pipeline_label": "Funil estreito",
        "risk_thin_pipeline_detail": "O funil ponderado está fraco frente à receita recente.",
        "risk_long_sales_cycle_label": "Ciclo de vendas longo",
        "risk_long_sales_cycle_detail": "As negociações levam em média {days} dias para serem ganhas.",
        "risk_overdue_tasks_label": "Tarefas atrasadas se acumulando",
        "risk_overdue_tasks_detail": "{n} tarefas estão atrasadas.",
        "risk_activity_falling_label": "Atividade em queda",
        "risk_activity_falling_detail": "Atividade registrada caiu {pct}%.",
        "risk_weak_conversion_label": "Conversão de leads fraca",
        "risk_weak_conversion_detail": "Apenas {pct}% dos novos leads converteram.",
        "rec_thin_pipeline_action": "Construa o funil",
        "rec_thin_pipeline_reason": (
            "O funil ponderado está fraco frente à receita recente; abra e qualifique "
            "mais negociações para proteger o próximo período."
        ),
        "rec_revenue_declining_action": "Foque no fechamento",
        "rec_revenue_declining_reason": (
            "A receita caiu em relação ao período anterior; priorize negociações perto "
            "da data de fechamento."
        ),
        "rec_low_win_rate_action": "Revise a qualificação e as perdas",
        "rec_low_win_rate_reason": (
            "Uma taxa de vitória baixa aponta para qualificação fraca ou um motivo de "
            "perda recorrente que vale investigar."
        ),
        "rec_long_sales_cycle_action": "Desbloqueie o estágio mais lento",
        "rec_long_sales_cycle_reason": (
            "As negociações passam muito tempo no funil; ataque o estágio de gargalo."
        ),
        "rec_weak_conversion_action": "Melhore o acompanhamento de leads",
        "rec_weak_conversion_reason": (
            "Poucos novos leads convertem; acelere o primeiro contato e a nutrição."
        ),
        "rec_overdue_tasks_action": "Zere o acúmulo de tarefas atrasadas",
        "rec_overdue_tasks_reason": (
            "Uma pilha crescente de tarefas atrasadas significa que o acompanhamento está falhando."
        ),
        "rec_activity_falling_action": "Aumente a prospecção",
        "rec_activity_falling_reason": (
            "A atividade registrada está caindo, e leva o funil junto."
        ),
    },
}

#: Period phrases, localized. Keyed by the English `Period.label` the analytics
#: layer produces. Phrased so the surrounding template reads naturally in each
#: language (e.g. Russian "за {period}", Portuguese parenthetical).
PERIOD_LABELS: dict[str, dict[str, str]] = {
    "today": {"en": "today", "ru": "сегодня", "pt-BR": "hoje"},
    "this week": {"en": "this week", "ru": "эту неделю", "pt-BR": "esta semana"},
    "this month": {"en": "this month", "ru": "этот месяц", "pt-BR": "este mês"},
    "this quarter": {"en": "this quarter", "ru": "этот квартал", "pt-BR": "este trimestre"},
    "this year": {"en": "this year", "ru": "этот год", "pt-BR": "este ano"},
    "the selected period": {
        "en": "the selected period",
        "ru": "выбранный период",
        "pt-BR": "o período selecionado",
    },
    "the period": {"en": "the period", "ru": "период", "pt-BR": "o período"},
}

#: Default pipeline stage names, localized. Keyed by the English seed name so a
#: custom stage a workspace renamed keeps its own text (falls through unchanged).
STAGE_LABELS: dict[str, dict[str, str]] = {
    "Qualification": {"ru": "Квалификация", "pt-BR": "Qualificação"},
    "Showing": {"ru": "Показ", "pt-BR": "Visita"},
    "Offer submitted": {"ru": "Оффер отправлен", "pt-BR": "Proposta enviada"},
    "Under contract": {"ru": "В договоре", "pt-BR": "Em contrato"},
    "Closing": {"ru": "Закрытие", "pt-BR": "Fechamento"},
    "Closed won": {"ru": "Закрыто — выиграно", "pt-BR": "Fechado ganho"},
    "Closed lost": {"ru": "Закрыто — проиграно", "pt-BR": "Fechado perdido"},
}


def normalize_locale(locale: str | None) -> str:
    """Map an incoming locale to one this catalog carries, defaulting to English.

    Accepts the exact frontend cookie values (`en`, `ru`, `pt-BR`) and is lenient
    about case and a bare `pt`, so a stray value degrades to English rather than
    raising.
    """
    if not locale:
        return DEFAULT_LOCALE
    value = locale.strip()
    if value in TEMPLATES:
        return value
    lowered = value.lower()
    if lowered.startswith("pt"):
        return "pt-BR"
    if lowered.startswith("ru"):
        return "ru"
    return DEFAULT_LOCALE


def render(locale: str, key: str, params: Mapping[str, object] | None = None) -> str:
    """Localize `key` for `locale`, interpolating `params`.

    Falls back to English, then to the key itself — the never-blank contract. A
    template referencing a parameter the caller did not supply degrades to the
    raw template rather than raising, so a copy edit can never crash a request.
    """
    loc = normalize_locale(locale)
    template = TEMPLATES.get(loc, {}).get(key) or TEMPLATES[DEFAULT_LOCALE].get(key) or key
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def period_label(locale: str, english_label: str) -> str:
    """Localize a `Period.label`. Unknown labels pass through unchanged."""
    loc = normalize_locale(locale)
    return PERIOD_LABELS.get(english_label, {}).get(loc, english_label)


def stage_label(locale: str, english_name: str) -> str:
    """Localize a default pipeline stage name. Custom names pass through."""
    loc = normalize_locale(locale)
    if loc == DEFAULT_LOCALE:
        return english_name
    return STAGE_LABELS.get(english_name, {}).get(loc, english_name)


__all__ = [
    "DEFAULT_LOCALE",
    "PERIOD_LABELS",
    "STAGE_LABELS",
    "TEMPLATES",
    "normalize_locale",
    "period_label",
    "render",
    "stage_label",
]
