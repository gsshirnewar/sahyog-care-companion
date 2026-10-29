"""Supabase access-token verification with strict issuer/audience checks."""

from __future__ import annotations

import os
from functools import cached_property
from typing import Any

import jwt
from jwt import PyJWKClient


class InvalidAccessToken(ValueError):
    """The bearer token is missing, invalid, expired, or misconfigured."""


class SupabaseJWTVerifier:
    """Verify Supabase JWTs using a configured JWKS URL or explicit local secret."""

    def __init__(
        self,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        jwks_url: str | None = None,
        secret: str | None = None,
        algorithms: tuple[str, ...] | None = None,
    ) -> None:
        self.issuer = (issuer or os.getenv("SUPABASE_JWT_ISSUER", "")).rstrip("/")
        self.audience = audience or os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")
        self.jwks_url = jwks_url or os.getenv("SUPABASE_JWKS_URL", "")
        self.secret = secret or os.getenv("SUPABASE_JWT_SECRET", "")
        configured_algorithms = algorithms or tuple(
            filter(None, os.getenv("SUPABASE_JWT_ALGORITHMS", "ES256,RS256").split(","))
        )
        self.algorithms = configured_algorithms
        if not self.issuer:
            supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
            if supabase_url:
                self.issuer = f"{supabase_url}/auth/v1"

    @cached_property
    def _jwks_client(self) -> PyJWKClient:
        if not self.jwks_url:
            raise InvalidAccessToken("SUPABASE_JWKS_URL is not configured")
        return PyJWKClient(self.jwks_url, cache_keys=True, timeout=5)

    def verify(self, token: str) -> dict[str, Any]:
        if not token or len(token) > 16_384:
            raise InvalidAccessToken("Bearer token is missing or too large")
        if not self.issuer or not self.audience:
            raise InvalidAccessToken("JWT issuer and audience must be configured")
        if not self.algorithms:
            raise InvalidAccessToken("At least one JWT algorithm must be configured")

        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if algorithm not in self.algorithms or algorithm == "none":
                raise InvalidAccessToken("JWT signing algorithm is not allowed")

            if self.secret:
                # Explicit secret mode is intended for local Supabase HS256 only.
                if algorithm != "HS256" or self.algorithms != ("HS256",):
                    raise InvalidAccessToken("Shared-secret verification requires HS256 only")
                signing_key: Any = self.secret
            else:
                signing_key = self._jwks_client.get_signing_key_from_jwt(token).key

            claims = jwt.decode(
                token,
                signing_key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
                leeway=5,
            )
            if not isinstance(claims, dict):
                raise InvalidAccessToken("JWT claims must be an object")
            return claims
        except InvalidAccessToken:
            raise
        except (jwt.PyJWTError, OSError, ValueError) as exc:
            raise InvalidAccessToken("Bearer token is invalid") from exc
