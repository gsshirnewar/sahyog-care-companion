"""Authenticated actor and request-purpose context for protected API handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.api.app.auth.jwt.verification import InvalidAccessToken, SupabaseJWTVerifier
from services.api.app.auth.roles import AppRole, AuthenticationError, map_app_role

_bearer = HTTPBearer(auto_error=False)
_verifier = SupabaseJWTVerifier()
_ALLOWED_PURPOSES = frozenset({"triage", "booking", "records", "scribe", "admin", "compliance"})


@dataclass(frozen=True, slots=True)
class RequestContext:
    actor_id: UUID
    role: AppRole
    subject_id: UUID | None
    purpose: str
    consent_ids: tuple[UUID, ...]
    trace_id: str
    idempotency_key: str | None
    claims: dict[str, Any]


def _uuid_claim(value: object, claim_name: str, *, optional: bool = False) -> UUID | None:
    if value is None and optional:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise AuthenticationError(f"JWT {claim_name} claim is invalid") from None


def _context_from_claims(request: Request, claims: dict[str, Any]) -> RequestContext:
    actor_id = _uuid_claim(claims.get("sub"), "sub")
    role = map_app_role(claims)
    purpose = claims.get("purpose")
    if not isinstance(purpose, str) or purpose not in _ALLOWED_PURPOSES:
        raise AuthenticationError("JWT has no recognized trusted purpose claim")

    raw_consent_ids = claims.get("consent_ids", [])
    if not isinstance(raw_consent_ids, list) or len(raw_consent_ids) > 100:
        raise AuthenticationError("JWT consent_ids claim is invalid")
    consent_ids = tuple(_uuid_claim(value, "consent_ids item") for value in raw_consent_ids)
    subject_id = _uuid_claim(claims.get("subject_id"), "subject_id", optional=True)

    return RequestContext(
        actor_id=actor_id,
        role=role,
        subject_id=subject_id,
        purpose=purpose,
        consent_ids=consent_ids,
        trace_id=getattr(request.state, "trace_id", ""),
        idempotency_key=getattr(request.state, "idempotency_key", None),
        claims=claims,
    )


async def get_request_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> RequestContext:
    """Verify bearer JWT and build context; caller-supplied role/purpose are ignored."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = _verifier.verify(credentials.credentials)
        return _context_from_claims(request, claims)
    except (InvalidAccessToken, AuthenticationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
