"""Model pricing, for cost accounting.

Cost is computed from token counts the provider reports, per model. The rates
live here rather than in configuration because they are facts about a model,
not choices about a deployment — and a rate that a config file could get wrong
is a cost ceiling that silently stops protecting anyone.

**Unknown models are priced high, not free.** The whole point of the number is
to feed a hard ceiling, and the dangerous direction to be wrong in is *under*.
A model this table has never heard of is charged at the most expensive known
rate, so a new model id someone forgot to add here trips the ceiling early
rather than running unbounded. Erring toward refusing spend is the correct
failure for a budget guard.

Rates are USD per **million** tokens, input and output separately, matching how
every model vendor quotes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ModelRate:
    #: USD per million input (prompt) tokens.
    input_per_mtok: Decimal
    #: USD per million output (completion) tokens.
    output_per_mtok: Decimal


#: Per-model rates. Keyed on the exact model id sent to the provider so there is
#: no fuzzy matching to get subtly wrong. Values are the published list prices
#: for the Claude family; a deployment on a negotiated rate does not change what
#: it *should* budget against, so these stay as the conservative public figures.
MODEL_RATES: dict[str, ModelRate] = {
    "claude-opus-4-8": ModelRate(Decimal("15"), Decimal("75")),
    "claude-sonnet-5": ModelRate(Decimal("3"), Decimal("15")),
    "claude-fable-5": ModelRate(Decimal("3"), Decimal("15")),
    "claude-haiku-4-5-20251001": ModelRate(Decimal("1"), Decimal("5")),
    # The echo provider costs nothing — it never calls a model — but is priced
    # so cost accounting has a rate for every model that can appear in a job
    # row, rather than falling through to the unknown-model default and logging
    # a warning on every local run.
    "echo": ModelRate(Decimal("0"), Decimal("0")),
}

#: What an unrecognised model is charged. The most expensive known input/output
#: rates, combined — see the module docstring for why high rather than zero.
_UNKNOWN_RATE = ModelRate(Decimal("15"), Decimal("75"))

#: Cost is rounded to the micro-dollar. Money, so Decimal throughout; six places
#: because a single cheap call can be worth a fraction of a cent and rounding it
#: to cents would make a month of them vanish.
_QUANTUM = Decimal("0.000001")


def rate_for(model: str) -> ModelRate:
    return MODEL_RATES.get(model, _UNKNOWN_RATE)


def is_known_model(model: str) -> bool:
    """Whether the model has a real rate. Used to warn on the unknown-model
    fallback rather than silently over-charging without a trace."""
    return model in MODEL_RATES


def cost_of(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """USD for one completion, from its token counts.

    Kept in Decimal from end to end: these figures are summed into a running
    monthly total that a ceiling is compared against, and a float that drifts by
    a rounding error per call drifts a whole tenant's budget over a month.
    """
    rate = rate_for(model)
    cost = (
        Decimal(prompt_tokens) / Decimal(1_000_000) * rate.input_per_mtok
        + Decimal(completion_tokens) / Decimal(1_000_000) * rate.output_per_mtok
    )
    return cost.quantize(_QUANTUM)


__all__ = ["MODEL_RATES", "ModelRate", "cost_of", "is_known_model", "rate_for"]
