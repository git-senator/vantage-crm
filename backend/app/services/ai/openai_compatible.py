"""OpenAI-compatible Chat Completions adapter, over httpx.

One adapter for every provider that speaks the OpenAI `/chat/completions` shape:
Google Gemini (its OpenAI-compatibility endpoint), Groq, OpenRouter, Cerebras,
Mistral, and OpenAI itself. Which one is reached is a matter of `AI_API_BASE`
and `AI_MODEL`, not of code — the wire format is identical, so a single adapter
serves them all and switching provider is two environment variables.

Written directly over httpx for the same reason as the Anthropic adapter: this
application already has one outbound HTTP client and one set of timeout, retry
and error conventions, and a vendor SDK would bring its own.

The one shape difference from Anthropic worth knowing: the OpenAI format has no
separate `system` field — the system prompt travels as the first message with
role `"system"`. The security posture (SECURITY.md §5) is unchanged: that first
message is authored by us and carries no CRM data; untrusted content still only
ever rides inside a `user` message, fenced by the prompt framework.

The API key is read once at construction from a `SecretStr`, so it never lands
in a log line, a repr, or an exception message — the adapter raises `AIError`
with the *provider's* error text, never the request that carried the key.
"""

from __future__ import annotations

import json

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.ai.base import (
    AIError,
    CompletionRequest,
    CompletionResult,
    TokenUsage,
    ToolCall,
)

logger = get_logger(__name__)

#: HTTP statuses that mean "try again later" rather than "this will never work".
#: 429 is rate limiting (the one a free tier hits first), 5xx is the provider's
#: problem — both transient. A 400 or 401 is ours and retrying only wastes the
#: budget.
_RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})

#: OpenAI `finish_reason` → the neutral `stop_reason` the rest of the system
#: uses. `length` is the truncation signal `CompletionResult.truncated` reads.
_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "content_filter",
}


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        # Trailing slash trimmed so the join is unambiguous whether the operator
        # set `.../v1beta/openai` or `.../v1beta/openai/`.
        self._base_url = settings.AI_API_BASE.rstrip("/")
        self._api_key = settings.AI_API_KEY.get_secret_value()
        self._timeout = settings.AI_TIMEOUT_SECONDS
        # Sent only when configured. A reasoning model (Gemini 3.x flash) spends
        # output tokens on hidden thinking; bounding it keeps the visible answer
        # from being truncated at the token cap. Empty → not sent, provider
        # default. A provider that does not reason ignores an unknown field.
        self._reasoning_effort = settings.AI_REASONING_EFFORT

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        if not self._api_key:
            # A missing key is a configuration fault, not a transient one — fail
            # closed and permanently rather than retrying an empty credential.
            raise AIError("AI_API_KEY is not configured.", retryable=False)

        # The OpenAI shape has no system field: the system prompt is the first
        # message. Authored by us, no CRM data — the fencing that protects the
        # user turns is unchanged.
        messages: list[dict[str, object]] = [
            {"role": "system", "content": request.system}
        ]
        messages.extend(
            {"role": message.role, "content": message.content}
            for message in request.messages
        )

        payload: dict[str, object] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        user_id = request.metadata.get("user_id")
        if user_id:
            # The OpenAI format's stable end-user handle for abuse monitoring and
            # cost attribution. Only a non-PII correlation id is ever set here.
            payload["user"] = user_id

        if request.tools:
            # Provider-neutral ToolSpec → the OpenAI function-tool shape. Only
            # sent when a tool is actually registered, so an ordinary completion
            # is byte-for-byte what it was before tools existed.
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters
                        or {"type": "object", "properties": {}},
                    },
                }
                for tool in request.tools
            ]

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "authorization": f"Bearer {self._api_key}",
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
            body = response.json()
            # OpenAI-shaped errors are `{"error": {"message": ...}}`; some
            # gateways nest a string instead. Handle both without trusting shape.
            error = body.get("error", {})
            detail = error.get("message") if isinstance(error, dict) else str(error)
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
            choices = body.get("choices") or []
            if not choices:
                # No choice at all is not the same as an empty answer — it is a
                # malformed response, and not retryable: the same body returns.
                raise AIError(
                    "The model returned no choices.", retryable=False
                )
            choice = choices[0]
            message = choice.get("message") or {}

            # `content` can be a string, or (rarely) a list of parts on some
            # gateways. Joined rather than assuming a string so a multi-part
            # answer is not silently dropped to empty.
            content = message.get("content")
            if isinstance(content, list):
                text = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            else:
                text = content or ""

            tool_calls = [
                ToolCall(
                    id=str(call.get("id", "")),
                    name=str(call.get("function", {}).get("name", "")),
                    arguments=_decode_arguments(
                        call.get("function", {}).get("arguments")
                    ),
                )
                for call in (message.get("tool_calls") or [])
            ]

            usage = body.get("usage") or {}
            finish = choice.get("finish_reason") or "stop"
            return CompletionResult(
                text=text,
                model=body.get("model", model),
                usage=TokenUsage(
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                ),
                stop_reason=_STOP_REASONS.get(finish, "end_turn"),
                provider=self.name,
                tool_calls=tool_calls,
            )
        except AIError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            # A response we cannot parse is not retryable — the same malformed
            # body will come back. Surface it rather than returning empty text
            # that a caller would treat as a real, if blank, answer.
            raise AIError(
                "The model returned an unreadable response.", retryable=False
            ) from exc

    async def verify_configuration(self) -> bool:
        # No universal, free health endpoint across these gateways, so
        # reachability is inferred from configuration presence. A real call to
        # check would spend tokens (and quota) on every readiness probe.
        return bool(self._api_key)


def _decode_arguments(raw: object) -> dict:  # type: ignore[type-arg]
    """Tool-call arguments arrive as a JSON *string* in the OpenAI shape.

    Decoded to a dict so the orchestration layer sees the same structure the
    Anthropic adapter hands it. A string that is not valid JSON yields an empty
    mapping rather than raising — a malformed tool call should not crash the
    whole completion parse.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


__all__ = ["OpenAICompatibleProvider"]
