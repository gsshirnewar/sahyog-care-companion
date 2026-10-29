from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from services.api.app.auth import context as auth_context
from services.api.app.auth.context import RequestContext, _context_from_claims, get_request_context
from services.api.app.auth.jwt.verification import InvalidAccessToken, SupabaseJWTVerifier
from services.api.app.auth.roles import AppRole, AuthenticationError, map_app_role
from services.api.app.middleware.request_context import RequestContextMiddleware

ISSUER = "https://supabase.example.test/auth/v1"
SECRET = "test-only-signing-secret-not-for-production"


def issue_token(**overrides: object) -> str:
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "sub": "10000000-0000-4000-8000-000000000001",
        "iss": ISSUER,
        "aud": "authenticated",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "role": "authenticated",
        "app_metadata": {"role": "patient"},
        "purpose": "records",
        "consent_ids": ["30000000-0000-4000-8000-000000000001"],
        "subject_id": "20000000-0000-4000-8000-000000000001",
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_verifier_accepts_valid_supabase_style_token() -> None:
    verifier = SupabaseJWTVerifier(
        issuer=ISSUER,
        audience="authenticated",
        secret=SECRET,
        algorithms=("HS256",),
    )
    claims = verifier.verify(issue_token())
    assert claims["purpose"] == "records"
    assert claims["role"] == "authenticated"


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: issue_token(exp=datetime.now(timezone.utc) - timedelta(minutes=1)),
        lambda: issue_token(iss="https://attacker.example.test"),
        lambda: issue_token(aud="anon"),
        lambda: jwt.encode(
            {
                "iss": ISSUER,
                "aud": "authenticated",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            SECRET,
            algorithm="HS256",
        ),
    ],
)
def test_verifier_rejects_bad_tokens(token_factory) -> None:
    verifier = SupabaseJWTVerifier(
        issuer=ISSUER,
        audience="authenticated",
        secret=SECRET,
        algorithms=("HS256",),
    )
    with pytest.raises(InvalidAccessToken):
        verifier.verify(token_factory())


def test_role_comes_from_app_metadata_not_user_metadata() -> None:
    assert map_app_role(
        {"role": "authenticated", "app_metadata": {"role": "clinician"}, "user_metadata": {"role": "admin"}}
    ) == AppRole.CLINICIAN
    with pytest.raises(AuthenticationError):
        map_app_role({"role": "authenticated", "user_metadata": {"role": "admin"}})


def test_context_rejects_untrusted_purpose_and_malformed_consent_ids() -> None:
    class State:
        trace_id = "trace-test-1"
        idempotency_key = None

    class Request:
        state = State()

    with pytest.raises(AuthenticationError):
        _context_from_claims(
            Request(),
            {"sub": "10000000-0000-4000-8000-000000000001", "role": "authenticated", "app_metadata": {"role": "patient"}},
        )
    with pytest.raises(AuthenticationError):
        _context_from_claims(
            Request(),
            {
                "sub": "10000000-0000-4000-8000-000000000001",
                "role": "authenticated",
                "app_metadata": {"role": "patient"},
                "purpose": "records",
                "consent_ids": ["not-a-uuid"],
            },
        )


def test_auth_dependency_integration_propagates_context_and_trace_id(monkeypatch) -> None:
    claims = {
        "sub": "10000000-0000-4000-8000-000000000001",
        "role": "authenticated",
        "app_metadata": {"role": "patient"},
        "purpose": "records",
        "consent_ids": ["30000000-0000-4000-8000-000000000001"],
    }

    class StubVerifier:
        def verify(self, token: str):
            assert token == "signed-test-token"
            return claims

    monkeypatch.setattr(auth_context, "_verifier", StubVerifier())
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/protected")
    async def protected(context: RequestContext = Depends(get_request_context)):
        return {
            "actor_id": str(context.actor_id),
            "role": context.role.value,
            "purpose": context.purpose,
            "consent_ids": [str(value) for value in context.consent_ids],
            "trace_id": context.trace_id,
            "idempotency_key": context.idempotency_key,
        }

    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(
                "/protected",
                headers={
                    "Authorization": "Bearer signed-test-token",
                    "X-Trace-ID": "trace-integration-001",
                    "Idempotency-Key": "booking-retry-1",
                },
            )

    response = asyncio.run(request())
    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "patient"
    assert payload["purpose"] == "records"
    assert payload["trace_id"] == "trace-integration-001"
    assert payload["idempotency_key"] == "booking-retry-1"
    assert response.headers["x-trace-id"] == "trace-integration-001"


def test_missing_bearer_is_401() -> None:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/protected")
    async def protected(context: RequestContext = Depends(get_request_context)):
        return {"actor": str(context.actor_id)}

    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/protected")

    response = asyncio.run(request())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
