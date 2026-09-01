from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from cherryfin.core.models import Evidence, EvidenceKind
from cherryfin.intelligence.models import EvidenceDocument, Instrument


class FilingType(StrEnum):
    ANNUAL_REPORT = "annual_report"
    QUARTERLY_REPORT = "quarterly_report"
    CURRENT_REPORT = "current_report"
    FINANCIAL_STATEMENTS = "financial_statements"
    XBRL = "xbrl"
    PROSPECTUS = "prospectus"
    OTHER = "other"


class FilingDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    uri: str = Field(min_length=1, max_length=2000)
    filing_type: FilingType
    published_at: datetime
    data_as_of: datetime | None = None
    trust_score: float = Field(default=0.95, ge=0, le=1)
    license_tag: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, max_length=30)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("uri")
    @classmethod
    def require_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise ValueError("filing URI must be an absolute HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("filing URI must not contain credentials")
        return value


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    content: bytes
    mime_type: str
    fetched_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        computed = hashlib.sha256(self.content).hexdigest()
        if self.content_sha256.casefold() != computed:
            raise ValueError("content_sha256 does not match fetched document content")


class FilingConnector(Protocol):
    async def discover(
        self,
        *,
        instrument: Instrument,
        since: datetime | None = None,
    ) -> list[FilingDescriptor]: ...

    async def fetch(self, descriptor: FilingDescriptor) -> FetchedDocument: ...


class AllowlistedHTTPDocumentFetcher:
    """Fetches only from explicit official hosts, with redirects disabled and a byte limit."""

    _ALLOWED_CONTENT_TYPES = (
        "application/json",
        "application/pdf",
        "application/xml",
        "application/xhtml+xml",
        "application/zip",
        "application/x-xbrl",
        "text/csv",
        "text/html",
        "text/plain",
        "text/xml",
    )

    def __init__(
        self,
        *,
        allowed_hosts: set[str],
        max_bytes: int = 20_000_000,
        timeout_seconds: float = 30.0,
        allow_subdomains: bool = False,
    ) -> None:
        normalized = {host.strip().casefold().rstrip(".") for host in allowed_hosts if host.strip()}
        if not normalized:
            raise ValueError("at least one allowed host is required")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._allowed_hosts = normalized
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._allow_subdomains = allow_subdomains

    def validate_uri(self, uri: str) -> str:
        parsed = urlsplit(uri)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not host:
            raise ValueError("document URI must use HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("document URI must not contain credentials")
        if parsed.port not in {None, 443}:
            raise ValueError("document URI must use the default HTTPS port")
        exact_match = host in self._allowed_hosts
        subdomain_match = self._allow_subdomains and any(
            host.endswith(f".{allowed}") for allowed in self._allowed_hosts
        )
        if not exact_match and not subdomain_match:
            raise ValueError(f"document host {host!r} is not allowlisted")
        return uri

    async def fetch(self, uri: str) -> FetchedDocument:
        validated_uri = self.validate_uri(uri)
        chunks: list[bytes] = []
        total = 0
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client, client.stream(
            "GET",
            validated_uri,
            headers={"Accept": "*/*", "User-Agent": "CherryFin/0.2"},
        ) as response:
            if response.is_redirect:
                raise ValueError("redirects are not permitted for filing retrieval")
            response.raise_for_status()
            mime_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
            supported_type = mime_type in self._ALLOWED_CONTENT_TYPES or (
                mime_type.startswith("application/")
                and (mime_type.endswith("+json") or mime_type.endswith("+xml"))
            )
            if not supported_type:
                raise ValueError(f"unsupported filing content type: {mime_type or 'missing'}")
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_bytes:
                    raise ValueError("filing exceeds the configured byte limit")
                chunks.append(chunk)

        content = b"".join(chunks)
        return FetchedDocument(
            content=content,
            mime_type=mime_type,
            fetched_at=datetime.now(UTC),
            content_sha256=hashlib.sha256(content).hexdigest(),
        )


class StaticFilingConnector:
    """Deterministic connector for tests, demos, and licensed offline data drops."""

    def __init__(
        self,
        *,
        descriptors: list[FilingDescriptor],
        documents: dict[str, FetchedDocument],
    ) -> None:
        self._descriptors = list(descriptors)
        self._documents = dict(documents)

    async def discover(
        self,
        *,
        instrument: Instrument,
        since: datetime | None = None,
    ) -> list[FilingDescriptor]:
        del instrument
        return [
            descriptor
            for descriptor in self._descriptors
            if since is None or descriptor.published_at >= since
        ]

    async def fetch(self, descriptor: FilingDescriptor) -> FetchedDocument:
        try:
            return self._documents[descriptor.external_id]
        except KeyError as exc:
            raise FileNotFoundError(descriptor.external_id) from exc


def filing_to_evidence_document(
    *,
    descriptor: FilingDescriptor,
    fetched: FetchedDocument,
) -> EvidenceDocument:
    evidence_id = "ev_" + hashlib.sha256(
        f"{descriptor.source_name}|{descriptor.external_id}|{fetched.content_sha256}".encode()
    ).hexdigest()[:24]
    return EvidenceDocument(
        evidence=Evidence(
            evidence_id=evidence_id,
            kind=EvidenceKind.OFFICIAL_FILING,
            source_name=descriptor.source_name,
            title=descriptor.title,
            uri=descriptor.uri,
            observed_at=fetched.fetched_at,
            data_as_of=descriptor.data_as_of,
            published_at=descriptor.published_at,
            trust_score=descriptor.trust_score,
            content_sha256=fetched.content_sha256,
            license_tag=descriptor.license_tag,
        ),
        content_bytes=fetched.content,
        mime_type=fetched.mime_type,
        language=descriptor.language,
        metadata={
            "external_id": descriptor.external_id,
            "filing_type": descriptor.filing_type.value,
            **descriptor.metadata,
        },
    )
