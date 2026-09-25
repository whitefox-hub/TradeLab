"""Authentication for one operator. Plan.md §20.3.

With a single identity this is not establishing *who*. It is a deliberate act
before a decision can be recorded, and it keeps the surface closed if the port
ever stops being local. Sessions are short-lived and every write reaches
`audit_log` with an actor, an action and a time.

What is lost by collapsing the roles is stated in §20.3 and is not nothing:
nothing here prevents lowering a gate and approving under it. That is handled in
the database, by a trigger, so it cannot be clicked past — see migration 0004.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "tradelab_session"
SESSION_MAX_AGE = timedelta(hours=8)


@dataclass(frozen=True, slots=True)
class Operator:
    email: str
    role: str = "admin"


def hash_password(password: str, salt: str) -> str:
    """PBKDF2 rather than a bare digest. One credential is still a credential."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 200_000
    ).hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    # Constant time: a timing oracle on one account is still a timing oracle.
    return hmac.compare_digest(hash_password(password, salt), expected)


class Sessions:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("session secret must be at least 32 characters")
        self._serializer = URLSafeTimedSerializer(secret, salt="tradelab-session")

    def issue(self, email: str) -> str:
        return self._serializer.dumps({"email": email, "at": datetime.now(UTC).isoformat()})

    def read(self, token: str) -> Operator | None:
        try:
            payload = self._serializer.loads(
                token, max_age=int(SESSION_MAX_AGE.total_seconds())
            )
        except (BadSignature, SignatureExpired):
            return None
        return Operator(email=payload["email"])


def new_secret() -> str:
    return secrets.token_urlsafe(48)


def operator_dependency(sessions: Sessions):
    """Builds the dependency, so the session signer is injected rather than global."""

    def current_operator(
        token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> Operator:
        operator = sessions.read(token) if token else None
        if operator is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="sign in required before a decision can be recorded",
            )
        return operator

    return Depends(current_operator)
