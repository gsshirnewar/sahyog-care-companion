"""Trusted role mapping from Supabase app_metadata claims."""

from enum import StrEnum


class AppRole(StrEnum):
    PATIENT = "patient"
    CLINICIAN = "clinician"
    FRONT_DESK = "front_desk"
    ADMIN = "admin"
    COMPLIANCE = "compliance"
    SECURITY_ADMIN = "security_admin"


class AuthenticationError(ValueError):
    """Raised when a verified token cannot be mapped to an application role."""


def map_app_role(claims: dict[str, object]) -> AppRole:
    """Map only server-managed app_metadata.role; never trust user_metadata.role."""
    auth_role = claims.get("role")
    if auth_role != "authenticated":
        raise AuthenticationError("JWT must represent an authenticated user")

    app_metadata = claims.get("app_metadata")
    raw_role = app_metadata.get("role") if isinstance(app_metadata, dict) else None
    try:
        return AppRole(raw_role)
    except (ValueError, TypeError):
        raise AuthenticationError("JWT has no recognized application role") from None
