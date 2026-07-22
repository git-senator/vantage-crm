"""MFA contracts.

Note what no response model contains: the TOTP secret, except in the single
enrolment response that exists to deliver it, and the recovery codes, except in
the single response that generates them. Both are write-once secrets — an
endpoint that could return them again would need them stored recoverably, which
defeats hashing the codes at all.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MfaEnrolmentStarted(BaseModel):
    """Everything needed to add the account to an authenticator app.

    Both fields carry the same secret — the URI for scanning, the bare secret
    for the "can't scan?" path. This is the only response that ever contains it.
    """

    secret: str
    provisioning_uri: str


class MfaActivate(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class MfaRecoveryCodes(BaseModel):
    """Shown once, at generation. There is no endpoint to see them again."""

    recovery_codes: list[str]


class MfaPasswordConfirm(BaseModel):
    """Removing or rotating a second factor re-authenticates.

    A session alone is not enough: an attacker holding a stolen one would use
    exactly these endpoints to make their access durable.
    """

    password: str = Field(min_length=1, max_length=256)


class MfaChallengeRequired(BaseModel):
    """The login response when a second factor is owed.

    Deliberately carries no user profile. Until the second factor is proved the
    caller has not authenticated, and returning a name or an avatar would leak
    that the password was correct — which is the one bit a credential-stuffing
    attacker most wants.
    """

    mfa_required: bool = True
    challenge_token: str
    expires_at: datetime


class MfaVerify(BaseModel):
    challenge_token: str = Field(min_length=1, max_length=2048)
    #: A TOTP code or a recovery code. One field, because the user does not
    #: think of them as different kinds of thing — they think "the code I have".
    code: str = Field(min_length=6, max_length=20)


class MfaStatus(BaseModel):
    enabled: bool
    enrolled_at: datetime | None
    recovery_codes_remaining: int
    #: True when the caller's roles oblige enrolment and they have not done it.
    #: The frontend routes on this rather than duplicating the role list.
    setup_required: bool
