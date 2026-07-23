"""Anthropic Messages API, over httpx.

Spoken to directly rather than through the vendor SDK, for the same reason the
WhatsApp channel talks to Meta over httpx: this application already has one
outbound HTTP client and one set of timeout, retry and error conventions, and a
second SDK would bring its own. The Messages API is a single POST; wrapping it
does not earn a dependency.

The API key is read once at construction from a `SecretStr`, so it never lands
in a log line, a repr, or an exception message — the adapter raises `AIError`
with the *provider's* error text, never the request that carried the key.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.ai.base import (
    AIError,
    CompletionRequest,
    CompletionResult,
    TokenUsage,
)

logger = get_logger(__name__)

#: The Messages API version header. Pinned, not floated: a silently newer API
#: shape is a production surprise, and bumping it is a deliberate change with a
#: changelog to read first.
_API_VERSION = "2023-06-01"

#: HTTP statuses that mean "try again later" rather than "this will never work".
#: 429 is rate limiting, 5xx is the provider's problem — both transient. A 400
#: or 401 is ours and retrying only wastes the budget.
_RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


class AnthropicCompletionProvider:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.AI_API_BASE.rstrip("/")
        self._api_key = settings.AI_API_KEY.get_secret_value()
        self._timeout = settings.AI_TIMEOUT_SECONDS

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        if not self._api_key:
            # A missing key is a configuration fault, not a transient one — fail
            # closed and permanently rather than retrying an empty credential.
            raise AIError("AI_API_KEY is not configured.", retryable=False)

        payload = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": request.system,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
        }
        if request.metadata:
            # The API accepts only `user_id` in metadata; anything else is
            # dropped. Passed through so cost attribution can carry a stable,
            # non-PII correlation id when a feature sets one.
            payload["metadata"] = {
                key: value
                for key, value in request.metadata.items()
                if key == "user_id"
            }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": _API_VERSION,
                        "content-type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AIError("The model timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            # Connection refused, DNS, reset mid-flight — transient transport
            # faults, all retryable, none of which should carry the request body
            # (and its key) into the exception.
            raise AIError("The model could not be reached.", retryable=True) from exc

        if response.status_code >= 400:
            raise self._error_from(response)

        return self._parse(response.json(), request.model)

    def _error_from(self, response: httpx.Response) -> AIError:
        retryable = response.status_code in _RETRYABLE_STATUSES
        message = f"The model returned {response.status_code}."
        try:
            detail = response.json().get("error", {}).get("message")
            if detail:
                # The provider's message, never the request. Truncated so a
                # verbose error cannot bloat a log line or a job's error column.
                message = f"{message} {detail[:300]}"
        except Exception:  # pragma: no cover — body may not be JSON
            logger.debug("ai_error_body_unparseable", exc_info=True)
        logger.warning(
            "ai_provider_error",
            extra={"status": response.status_code, "retryable": retryable},
        )
        return AIError(message, retryable=retryable)

    def _parse(self, body: dict, model: str) -> CompletionResult:  # type: ignore[type-arg]
        try:
            # `content` is a list of blocks; a text completion has text blocks.
            # Joined rather than assuming one, so a multi-block answer is not
            # silently truncated to its first block.
            text = "".join(
                block.get("text", "")
                for block in body.get("content", [])
                if block.get("type") == "text"
            )
            usage = body.get("usage", {})
            return CompletionResult(
                text=text,
                model=body.get("model", model),
                usage=TokenUsage(
                    prompt_tokens=int(usage.get("input_tokens", 0)),
                    completion_tokens=int(usage.get("output_tokens", 0)),
                ),
                stop_reason=str(body.get("stop_reason", "end_turn")),
                provider=self.name,
            )
        except (KeyError, TypeError, ValueError) as exc:
            # A response we cannot parse is not retryable — the same malformed
            # body will come back. Surface it rather than returning empty text
            # that a caller would treat as a real, if blank, answer.
            raise AIError("The model returned an unreadable response.", retryable=False) from exc

    async def verify_configuration(self) -> bool:
        # No dedicated health endpoint on the Messages API, so reachability is
        # inferred from configuration presence. A real call to check would spend
        # tokens on every readiness probe, which is the wrong trade.
        return bool(self._api_key)


__all__ = ["AnthropicCompletionProvider"]
