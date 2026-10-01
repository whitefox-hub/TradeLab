"""Single-operator authentication and short-lived signed sessions. Plan.md §20.3.

Lowering a gate then approving under it is blocked by a DB trigger (migration 0004), not here.
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
    """PBKDF2-SHA256 hex digest of `password` with `salt`."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 200_000
    ).hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
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
    """FastAPI dependency yielding the signed-in Operator; the signer is injected, not global."""

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
