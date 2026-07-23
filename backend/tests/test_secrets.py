"""Envelope encryption for secrets at rest.

Pure unit tests — no database, no AWS. What they pin down:

  * **Sealing is authenticated.** A tampered token fails loudly instead of
    decrypting to plausible garbage.
  * **Context binds a value to its column.** A sealed secret lifted into another
    field does not open there.
  * **Rotation does not require a rewrite.** The key id travels in the token, so
    values sealed under an old key stay readable after the active key changes.
  * **Legacy plaintext still reads.** The feature ships onto a database that
    already holds unencrypted TOTP secrets, and refusing them would lock every
    enrolled user out at deploy time.
"""

from __future__ import annotations

import base64
import os

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.secrets import (
    DERIVED_KEY_ID,
    LocalKeyProvider,
    SecretBox,
    SecretsError,
    build_key_provider,
    encryption_configured,
    is_token,
)

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _settings(**overrides: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        JWT_SECRET=SecretStr(TEST_JWT_SECRET),
        ENVIRONMENT="test",
        **overrides,
    )


def _box(**overrides: object) -> SecretBox:
    return SecretBox(build_key_provider(_settings(**overrides)))


class TestRoundTrip:
    def test_a_value_survives_sealing(self) -> None:
        box = _box()
        sealed = box.encrypt("JBSWY3DPEHPK3PXP", context="users.mfa_secret")

        assert is_token(sealed)
        assert "JBSWY3DPEHPK3PXP" not in sealed
        assert box.decrypt(sealed, context="users.mfa_secret") == "JBSWY3DPEHPK3PXP"

    def test_the_same_value_seals_differently_each_time(self) -> None:
        """A fresh nonce per encryption. Without it, two users with the same
        secret produce the same ciphertext, which leaks that they match."""
        box = _box()
        assert box.encrypt("same") != box.encrypt("same")

    def test_unicode_survives(self) -> None:
        box = _box()
        assert box.decrypt(box.encrypt("naïve — 秘密")) == "naïve — 秘密"


class TestAuthentication:
    def test_a_tampered_ciphertext_is_rejected(self) -> None:
        """AES-GCM is authenticated: a flipped bit is an error, not garbage that
        decrypts to something plausible."""
        box = _box()
        sealed = box.encrypt("secret-value")

        prefix, key_id, wrapped, nonce, ciphertext = sealed.split(".", 4)
        flipped = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")
        tampered = ".".join((prefix, key_id, wrapped, nonce, flipped))

        with pytest.raises(SecretsError, match="altered"):
            box.decrypt(tampered)

    def test_context_binds_a_value_to_its_column(self) -> None:
        """A sealed mfa_secret moved into another field must not work there."""
        box = _box()
        sealed = box.encrypt("secret-value", context="users.mfa_secret")

        with pytest.raises(SecretsError):
            box.decrypt(sealed, context="users.something_else")

    def test_a_malformed_token_is_a_clear_error(self) -> None:
        box = _box()
        with pytest.raises(SecretsError, match="Malformed"):
            box.decrypt("vnt1.only-two-parts")


class TestKeyManagement:
    def test_rotation_keeps_old_values_readable(self) -> None:
        """The key id travels in the token, so changing the active key is a
        config change rather than a migration that must rewrite every row."""
        old, new = _key(), _key()

        sealed_with_old = _box(
            ENCRYPTION_KEYS=SecretStr(f"old:{old}"),
            ENCRYPTION_ACTIVE_KEY_ID="old",
        ).encrypt("historic")

        after_rotation = _box(
            ENCRYPTION_KEYS=SecretStr(f"old:{old},new:{new}"),
            ENCRYPTION_ACTIVE_KEY_ID="new",
        )
        assert after_rotation.decrypt(sealed_with_old) == "historic"
        # New writes use the new key.
        assert ".new." in after_rotation.encrypt("current")

    def test_removing_a_key_that_still_has_rows_says_so(self) -> None:
        """"Decryption failed" would send somebody hunting for corruption when
        the answer is that a key was deleted from configuration."""
        old, new = _key(), _key()
        sealed = _box(
            ENCRYPTION_KEYS=SecretStr(f"old:{old}"), ENCRYPTION_ACTIVE_KEY_ID="old"
        ).encrypt("orphaned")

        with pytest.raises(SecretsError, match="No encryption key configured"):
            _box(
                ENCRYPTION_KEYS=SecretStr(f"new:{new}"),
                ENCRYPTION_ACTIVE_KEY_ID="new",
            ).decrypt(sealed)

    def test_an_active_key_id_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(SecretsError, match="not among the configured keys"):
            LocalKeyProvider({"a": os.urandom(32)}, "b")

    def test_a_short_key_is_refused(self) -> None:
        """A passphrase base64'd into 16 bytes is a low-entropy key that looks
        like a proper one."""
        short = base64.urlsafe_b64encode(os.urandom(16)).decode()
        with pytest.raises(SecretsError, match="32 required"):
            build_key_provider(
                _settings(
                    ENCRYPTION_KEYS=SecretStr(f"weak:{short}"),
                    ENCRYPTION_ACTIVE_KEY_ID="weak",
                )
            )

    def test_a_malformed_entry_is_refused(self) -> None:
        with pytest.raises(SecretsError, match="id:base64-key"):
            build_key_provider(_settings(ENCRYPTION_KEYS=SecretStr("no-colon-here")))

    def test_with_no_configuration_a_key_is_derived(self) -> None:
        """So a developer clone encrypts by default rather than silently storing
        plaintext until somebody remembers to set a variable."""
        sealed = _box().encrypt("value")
        assert f".{DERIVED_KEY_ID}." in sealed

    def test_kms_without_a_key_id_is_refused(self) -> None:
        with pytest.raises(SecretsError, match="KMS_KEY_ID"):
            build_key_provider(_settings(ENCRYPTION_PROVIDER="aws_kms"))


class TestLegacyPlaintext:
    def test_a_plaintext_value_is_returned_unchanged(self) -> None:
        """The migration path. Refusing these would lock every enrolled user out
        of their account the moment this deploys."""
        assert _box().decrypt("JBSWY3DPEHPK3PXP") == "JBSWY3DPEHPK3PXP"

    def test_plaintext_is_distinguishable_from_a_token(self) -> None:
        assert not is_token("JBSWY3DPEHPK3PXP")
        assert is_token(_box().encrypt("x"))


class TestProductionReadiness:
    def test_encryption_is_reported_as_configured_by_default(self) -> None:
        """`encryption_configured` reports rather than raises — both callers
        (readiness, admin health) want to surface a misconfiguration."""
        assert encryption_configured(_settings()) is True

    def test_a_broken_configuration_reports_false_rather_than_raising(self) -> None:
        assert (
            encryption_configured(
                _settings(ENCRYPTION_PROVIDER="aws_kms", KMS_KEY_ID=None)
            )
            is False
        )

    def test_production_refuses_the_local_provider(self) -> None:
        """The key lives in the process environment; anything that reads the
        environment reads the key."""
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            JWT_SECRET=SecretStr(TEST_JWT_SECRET),
            ENVIRONMENT="production",
            ENCRYPTION_PROVIDER="local",
        )
        with pytest.raises(RuntimeError, match="ENCRYPTION_PROVIDER is 'local'"):
            settings.assert_production_ready()
