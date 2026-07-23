"""Deterministic adapter for local development and tests.

The AI counterpart of `ConsoleEmailProvider`: it never calls a model, needs no
API key, and cannot leak a customer's data to a third party during a test run —
the single most serious class of AI-integration accident, and the reason the
default provider is this one rather than a real vendor.

It echoes a short, deterministic acknowledgement of the last user turn and
reports plausible token counts, so the whole stack around it — budget checks,
usage recording, cost accounting, the worker path — is exercised end to end with
a stable, assertable result. It is not a model and makes no attempt to be one.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.services.ai.base import CompletionRequest, CompletionResult, TokenUsage

logger = get_logger(__name__)

#: A crude token estimate: ~4 characters per token is the usual rule of thumb,
#: close enough for the echo path where the exact number does not matter.
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


class EchoCompletionProvider:
    name = "echo"

    def __init__(self) -> None:
        # Retained so a test can assert on exactly what would have been sent to
        # a real model — the same affordance the console email provider gives.
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)

        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"),
            "",
        )
        # Deterministic and obviously synthetic, so nobody mistakes an echo run
        # for a real answer in a screenshot.
        text = f"[echo] Acknowledged: {last_user.strip()[:200]}"

        prompt_tokens = _estimate_tokens(request.system) + sum(
            _estimate_tokens(m.content) for m in request.messages
        )
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=_estimate_tokens(text),
        )

        logger.info(
            "ai_echo_completion",
            extra={
                "model": request.model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            },
        )
        return CompletionResult(
            text=text,
            model=request.model,
            usage=usage,
            stop_reason="end_turn",
            provider=self.name,
        )

    async def verify_configuration(self) -> bool:
        return True


__all__ = ["EchoCompletionProvider"]
