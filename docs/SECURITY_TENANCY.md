# CherryFin Security, Tenancy, and Integrity Boundary

CherryFin uses database-per-tenant isolation while SQLite is the persistence backend. The default
tenant may keep the configured database path for compatibility. Every other tenant receives a
separate database file whose name is derived from a SHA-256 digest of the normalized tenant ID.

## Authentication

All `/v1/*` endpoints require an authenticated principal outside development and test. Credentials
are configured with `CHERRYFIN_TENANT_CREDENTIALS` as JSON. A credential fixes the tenant and role;
the API never accepts a caller-selected role header.

Supported roles:

- `viewer`
- `analyst`
- `data_ingestor`
- `reviewer`
- `tenant_admin`
- `platform_admin`

The platform administrator key is configured separately with `CHERRYFIN_ADMIN_API_KEY`. API keys
must come from a secret manager and must not be placed in prompts, evidence, logs, or repository
files.

Example request headers:

```text
X-CherryFin-Tenant: acme
X-CherryFin-Actor: analyst@example.com
X-CherryFin-Key: <tenant credential>
X-Request-ID: <caller correlation ID>
```

## Trust boundary

The public analysis endpoint rejects inline financial claims. Verified claims must be ingested into
the authenticated tenant's ledger and retrieved through `knowledge_context`.

Inline evidence is always rewritten as `user_provided`, moved into a `user:` ID namespace, stripped
of URI, content hash, and license claims, and capped by `CHERRYFIN_MAX_INLINE_EVIDENCE_TRUST`.
Caller-controlled data therefore cannot impersonate a regulator, exchange, licensed feed, or
official filing. Market analysis backed only by user-provided, news, or model-inference evidence is
confidence-capped and cannot pass the verified-source release gate.

Ledger hydration loads canonical claims first. Any inline evidence ID collision with ledger-owned
evidence is rejected rather than merged.

## Time boundary

API tenant stores run with untrusted client timestamps disabled:

- evidence `ingested_at` is assigned by the server;
- claims `asserted_at` are assigned by the server;
- knowledge-time dependency checks use server ingestion time;
- claim status history is append-only and cannot replace an earlier event.

Direct library stores retain the legacy trusted-timestamp mode by default for deterministic tests
and offline historical imports. Production API code never enables that mode.

## Atomic writes

Evidence ingestion, statement extraction, claim writes, contradiction creation, and status changes
run under one `BEGIN IMMEDIATE` transaction. Nested store writes join the same transaction. Any
exception rolls back the entire ingestion.

Contradiction resolution also updates both claims and the contradiction record in one transaction.

## Evidence integrity

An evidence document may contain exactly one payload representation: text, bytes, or structured
JSON. A hash-only envelope is allowed for externally retained content. CherryFin computes:

1. the payload SHA-256;
2. a record SHA-256 over canonical evidence metadata, payload hash, MIME type, language, and public
   metadata.

Structured payloads and raw content are returned only when the caller has
`evidence:content:read`.

## Calculation boundary

The language model is not a calculation authority. Until the server-side calculation registry is
added, analysis responses containing model-created calculation records fail deterministic policy.
The existing calculator endpoints remain server-executed and available to authorized principals.

## Audit chain

Every mutation appends an audit event containing tenant, actor, request ID, action, resource,
payload hash, previous event hash, and current event hash. `/v1/audit/verify` recomputes the chain.
The local chain detects accidental or unsophisticated tampering; production deployments should
periodically sign or externally anchor the latest hash with a KMS/HSM-backed key and immutable
storage.

## Remaining production work

This PR does not claim to provide external identity federation, KMS encryption, PostgreSQL row-level
security, object storage, signed audit anchoring, or rate limiting. Those remain required before a
public financial service deployment.
