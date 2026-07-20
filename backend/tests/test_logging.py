"""Log redaction and correlation.

The redaction tests are security tests, not formatting tests: a regression here
puts credentials into a log aggregator, where they persist far longer than in
any database.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import JsonFormatter, redact, request_id_var, user_id_var


class TestRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "PASSWORD",
            "user_password",
            "jwt_secret",
            "access_token",
            "refresh_token",
            "Authorization",
            "cookie",
            "csrf_token",
            "api_key",
            "aws_secret_access_key",
            "ssn",
            "credit_card",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        assert redact({key: "sensitive-value"})[key] == "[REDACTED]"

    def test_ordinary_keys_survive(self) -> None:
        payload = {"email": "agent@example.com", "lead_id": 42}
        assert redact(payload) == payload

    def test_redacts_nested_structures(self) -> None:
        result = redact({"outer": {"inner": {"password": "hunter2", "keep": "yes"}}})
        assert result["outer"]["inner"]["password"] == "[REDACTED]"
        assert result["outer"]["inner"]["keep"] == "yes"

    def test_redacts_inside_lists(self) -> None:
        result = redact({"users": [{"name": "a", "token": "leak"}]})
        assert result["users"][0]["token"] == "[REDACTED]"
        assert result["users"][0]["name"] == "a"

    def test_depth_is_capped(self) -> None:
        """Guards against a cyclic or pathological structure hanging the logger."""
        deep: dict = {}
        cursor = deep
        for _ in range(20):
            cursor["next"] = {}
            cursor = cursor["next"]
        assert "TRUNCATED" in json.dumps(redact(deep))


class TestJsonFormatter:
    def _record(self, **extra: object) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="event_happened", args=(), exc_info=None, func="test_fn",
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_emits_valid_json(self) -> None:
        payload = json.loads(JsonFormatter().format(self._record()))
        assert payload["level"] == "INFO"
        assert payload["message"] == "event_happened"
        assert "timestamp" in payload
        assert "source" in payload

    def test_includes_correlation_ids(self) -> None:
        request_token = request_id_var.set("req-123")
        user_token = user_id_var.set("user-456")
        try:
            payload = json.loads(JsonFormatter().format(self._record()))
            assert payload["request_id"] == "req-123"
            assert payload["user_id"] == "user-456"
        finally:
            request_id_var.reset(request_token)
            user_id_var.reset(user_token)

    def test_omits_correlation_when_unset(self) -> None:
        assert "request_id" not in json.loads(JsonFormatter().format(self._record()))

    def test_redacts_extra_context(self) -> None:
        """`logger.info(..., extra={"password": ...})` must not leak."""
        formatted = JsonFormatter().format(
            self._record(password="hunter2", lead_id="L-1")
        )
        assert "hunter2" not in formatted
        payload = json.loads(formatted)
        assert payload["context"]["password"] == "[REDACTED]"
        assert payload["context"]["lead_id"] == "L-1"
