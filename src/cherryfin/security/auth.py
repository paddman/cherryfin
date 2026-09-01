from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TENANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class AuthenticationError(RuntimeError):
    """Raised when an API credential cannot be authenticated."""


class AuthorizationError(RuntimeError):
    """Raised when an authenticated principal lacks a required scope."""


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    DATA_INGESTOR = "data_ingestor"
    REVIEWER = "reviewer"
    TENANT_ADMIN = "tenant_admin"
    PLATFORM_ADMIN = "platform_admin"


class Scope(StrEnum):
    CAPABILITIES_READ = "capabilities:read"
    ANALYSIS_RUN = "analysis:run"
    CALCULATOR_RUN = "calculator:run"
    INSTRUMENT_READ = "instrument:read"
    INSTRUMENT_WRITE = "instrument:write"
    EVIDENCE_READ = "evidence:read"
    EVIDENCE_CONTENT_READ = "evidence:content:read"
    EVIDENCE_WRITE = "evidence:write"
    CLAIM_READ = "claim:read"
    CLAIM_WRITE = "claim:write"
    CONTRADICTION_READ = "contradiction:read"
    CONTRADICTION_RESOLVE = "contradiction:resolve"
    AUDIT_READ = "audit:read"


_VIEW_SCOPES = {
    Scope.CAPABILITIES_READ,
    Scope.INSTRUMENT_READ,
    Scope.EVIDENCE_READ,
    Scope.CLAIM_READ,
    Scope.CONTRADICTION_READ,
}
_ANALYST_SCOPES = _VIEW_SCOPES | {
    Scope.ANALYSIS_RUN,
    Scope.CALCULATOR_RUN,
}
_INGESTOR_SCOPES = _ANALYST_SCOPES | {
    Scope.INSTRUMENT_WRITE,
    Scope.EVIDENCE_WRITE,
    Scope.CLAIM_WRITE,
}
_REVIEWER_SCOPES = _ANALYST_SCOPES | {
    Scope.EVIDENCE_CONTENT_READ,
    Scope.CONTRADICTION_RESOLVE,
    Scope.AUDIT_READ,
}
_TENANT_ADMIN_SCOPES = set(Scope)
_ROLE_SCOPES: dict[Role, frozenset[Scope]] = {
    Role.VIEWER: frozenset(_VIEW_SCOPES),
    Role.ANALYST: frozenset(_ANALYST_SCOPES),
    Role.DATA_INGESTOR: frozenset(_INGESTOR_SCOPES),
    Role.REVIEWER: frozenset(_REVIEWER_SCOPES),
    Role.TENANT_ADMIN: frozenset(_TENANT_ADMIN_SCOPES),
    Role.PLATFORM_ADMIN: frozenset(Scope),
}


def normalize_tenant_id(value: str) -> str:
    normalized = value.strip().casefold()
    if not _TENANT_PATTERN.fullmatch(normalized):
        raise AuthenticationError(
            "tenant ID must start with a letter or digit and contain only "
            "letters, digits, dot, underscore, or hyphen"
        )
    return normalized


def normalize_actor_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise AuthenticationError("actor ID must contain between 1 and 200 characters")
    if any(ord(character) < 32 for character in normalized):
        raise AuthenticationError("actor ID must not contain control characters")
    return normalized


class TenantCredential(BaseModel):
    """A tenant-scoped service credential loaded from protected configuration."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=8, max_length=500)
    role: Role = Role.TENANT_ADMIN
    actor_id: str | None = Field(default=None, max_length=200)

    @field_validator("actor_id")
    @classmethod
    def validate_actor_id(cls, value: str | None) -> str | None:
        return normalize_actor_id(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: str
    actor_id: str
    role: Role
    platform_admin: bool = False

    def has_scope(self, scope: Scope) -> bool:
        return scope in _ROLE_SCOPES[self.role]


class AuthService:
    """Authenticates fixed-role tenant credentials without trusting role headers."""

    def __init__(
        self,
        *,
        environment: str,
        default_tenant_id: str,
        platform_admin_api_key: str,
        tenant_credentials: dict[str, TenantCredential],
    ) -> None:
        self._environment = environment.strip().casefold()
        self._default_tenant_id = normalize_tenant_id(default_tenant_id)
        self._platform_admin_api_key = platform_admin_api_key
        normalized_credentials: dict[str, TenantCredential] = {}
        for tenant_id, credential in tenant_credentials.items():
            normalized = normalize_tenant_id(tenant_id)
            if normalized in normalized_credentials:
                raise ValueError(f"duplicate normalized tenant credential: {normalized}")
            normalized_credentials[normalized] = credential
        self._tenant_credentials = normalized_credentials

    @property
    def development_bypass_enabled(self) -> bool:
        return (
            self._environment in {"development", "test"}
            and not self._platform_admin_api_key
            and not self._tenant_credentials
        )

    def authenticate(
        self,
        *,
        tenant_id: str | None,
        actor_id: str | None,
        api_key: str | None,
    ) -> Principal:
        tenant = normalize_tenant_id(tenant_id or self._default_tenant_id)

        if (
            self._platform_admin_api_key
            and api_key is not None
            and secrets.compare_digest(api_key, self._platform_admin_api_key)
        ):
            actor = normalize_actor_id(actor_id or "platform-admin")
            return Principal(
                tenant_id=tenant,
                actor_id=actor,
                role=Role.PLATFORM_ADMIN,
                platform_admin=True,
            )

        credential = self._tenant_credentials.get(tenant)
        if (
            credential is not None
            and api_key is not None
            and secrets.compare_digest(api_key, credential.api_key)
        ):
            actor = credential.actor_id or actor_id
            if actor is None:
                raise AuthenticationError(
                    "X-CherryFin-Actor is required for credentials without a fixed actor"
                )
            return Principal(
                tenant_id=tenant,
                actor_id=normalize_actor_id(actor),
                role=credential.role,
            )

        if self.development_bypass_enabled:
            return Principal(
                tenant_id=tenant,
                actor_id=normalize_actor_id(actor_id or "development"),
                role=Role.PLATFORM_ADMIN,
                platform_admin=True,
            )

        raise AuthenticationError("invalid or missing CherryFin API credential")

    @staticmethod
    def authorize(principal: Principal, scope: Scope) -> None:
        if not principal.has_scope(scope):
            raise AuthorizationError(
                f"role {principal.role.value} does not have required scope {scope.value}"
            )
