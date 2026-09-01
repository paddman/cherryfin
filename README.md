# CherryFin

**Tenant-isolated financial intelligence. Evidence first. Human controlled.**

CherryFin is a provider-neutral foundation for building high-assurance financial agents. It
separates source evidence, verified claims, deterministic calculations, model synthesis, policy,
human approval, and execution instead of treating a fluent model response as a financial control.

Version `0.3.0` adds the security and integrity boundary around the point-in-time evidence and
claims ledger introduced in version `0.2.0`.

## Current capabilities

- Thai and English routing for personal CFO, investment research, portfolio risk, business CFO,
  and trading-research workflows
- OpenAI-compatible model adapter for LM Studio, Ollama, vLLM, SGLang, and approved hosted models
- Immutable evidence envelopes with SHA-256 payload and canonical-record integrity
- Typed financial claims with evidence lineage and business-time/knowledge-time retrieval
- Contradiction detection, disputed status, explicit supersession, and human resolution
- Thai/English financial-statement row normalization
- Official-filing and licensed-market-data connector contracts
- Deterministic growth, loan, and historical portfolio-risk calculators
- Database-per-tenant isolation for the SQLite deployment
- Tenant credentials, fixed roles, and scope-based authorization
- Atomic evidence/claim/contradiction writes using `BEGIN IMMEDIATE`
- Server-controlled ingestion and assertion timestamps in the API path
- Hash-chained audit events with tenant, actor, request, resource, and payload hashes
- Verified-source release gates for market and investment analysis
- FastAPI, Docker, persistent storage, tests, and read-only CI verification

## Architecture

```text
Official filings / licensed data / approved user input
                         |
                         v
         Authentication + tenant + source policy
                         |
                         v
       Integrity validation + server knowledge time
                         |
                         v
   Tenant evidence store + typed bitemporal claims ledger
                         |
             +-----------+-----------+
             |                       |
             v                       v
   Contradiction lifecycle     Point-in-time retrieval
             |                       |
             +-----------+-----------+
                         v
       Canonical claim/evidence context for Cherry
                         |
                         v
        LLM synthesis under deterministic policy
                         |
                         v
     Evaluation gate + human-controlled proposals
```

The analysis API never authorizes a bank transfer, payment, or market order. No bank, broker,
payment, custody, vault, or signing credential is exposed to the model.

## Trust model

CherryFin distinguishes source categories rather than trusting whatever label a caller places on a
document.

- Evidence ingested through reviewed regulator, exchange, official-filing, company-disclosure, or
  licensed-data paths may satisfy the verified-source gate.
- Evidence supplied directly to `/v1/analyze` is rewritten as `user_provided`, assigned a `user:`
  identifier, stripped of claimed URI/hash/license metadata, and confidence-capped.
- Inline claims are rejected by the public analysis endpoint. Verified claims must be loaded from
  the authenticated tenant ledger through `knowledge_context`.
- A caller cannot replace a ledger claim or evidence record by reusing its ID.
- Model-created calculations are rejected by policy until they can reference a server-side
  calculation-registry artifact. The dedicated calculator endpoints continue to run deterministic
  code directly.

## Authentication and tenancy

All `/v1/*` endpoints require authentication outside development/test. Health and root endpoints
remain public for orchestration.

Configure tenant credentials as JSON:

```dotenv
CHERRYFIN_ENVIRONMENT=production
CHERRYFIN_DEFAULT_TENANT_ID=default
CHERRYFIN_ADMIN_API_KEY=<platform-admin-secret>
CHERRYFIN_TENANT_CREDENTIALS={"acme":{"api_key":"<tenant-secret>","role":"analyst","actor_id":"acme-agent"}}
```

Request headers:

```text
X-CherryFin-Tenant: acme
X-CherryFin-Actor: analyst@example.com
X-CherryFin-Key: <tenant credential>
X-Request-ID: <correlation ID>
```

Roles are fixed in protected configuration. The API does not accept a role or scope header from the
caller.

Supported roles:

- `viewer`
- `analyst`
- `data_ingestor`
- `reviewer`
- `tenant_admin`
- `platform_admin`

While SQLite remains the backend, CherryFin gives each tenant a separate database file. A future
PostgreSQL backend can preserve the same boundary using row-level security and composite tenant
keys.

## Point-in-time semantics

CherryFin keeps financial time separate from knowledge time:

- `effective_at`: when a claim applies in the financial world
- `data_as_of`: the date represented by source data
- `observed_at`: the source timestamp reported by a connector or caller
- `ingested_at`: when the CherryFin server accepted the evidence
- `asserted_at`: when the server accepted the claim
- `business_as_of`: the financial date under analysis
- `knowledge_as_of`: the latest information a replay may use

Production API stores assign `ingested_at` and `asserted_at` using the server clock. Backfilled
source dates remain source metadata and cannot rewrite CherryFin's transaction-time history.

## Start locally

```bash
git switch main
git pull --ff-only
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
pytest
uvicorn cherryfin.api.main:app --host 0.0.0.0 --port 8080
```

Example local model configuration:

```dotenv
CHERRYFIN_LLM_BASE_URL=http://127.0.0.1:1234/v1
CHERRYFIN_LLM_API_KEY=local
CHERRYFIN_LLM_MODEL=qwen3.5-35b
```

A development instance with no configured API credentials uses an explicit development bypass.
The bypass is disabled as soon as any tenant credential or platform-admin key is configured.

## Docker

```bash
docker compose up --build
```

The Compose deployment mounts persistent data at `/var/lib/cherryfin`, keeps the remaining
filesystem read-only, and runs the service without elevated Linux capabilities.

## Main APIs

```text
GET  /health
GET  /v1/capabilities
POST /v1/analyze

POST /v1/instruments
GET  /v1/instruments/resolve

POST /v1/evidence/ingest
GET  /v1/evidence/{evidence_id}

POST /v1/claims
POST /v1/claims/query

GET  /v1/contradictions
POST /v1/contradictions/{contradiction_id}/resolve

GET  /v1/audit/events
GET  /v1/audit/verify
GET  /v1/audit/snapshot

POST /v1/calculators/compound-growth
POST /v1/calculators/loan
POST /v1/calculators/portfolio-risk
```

### Point-in-time analysis request

```json
{
  "query": "วิเคราะห์แนวโน้มรายได้และความเสี่ยงจากข้อมูลที่มี ณ วันนั้น",
  "mode": "investment_research",
  "requested_as_of": "2025-12-31T23:59:59Z",
  "knowledge_context": {
    "subject_id": "issuer:abc",
    "predicates": ["revenue", "net_income", "operating_cash_flow"],
    "business_as_of": "2025-12-31T23:59:59Z",
    "knowledge_as_of": "2026-02-20T00:00:00Z"
  }
}
```

The API retrieves eligible tenant claims and supporting evidence, attaches an audit snapshot, and
then calls the model. Fabricated claim IDs, evidence IDs, future knowledge, retracted claims,
unsupported confidence, and unregistered calculation records fail deterministic policy.

## Verification

```bash
python -m compileall -q src tests
ruff check .
ruff format --check .
pytest
```

The tests cover authentication, authorization, tenant isolation, evidence integrity, payload
redaction, claim dependencies, bitemporal retrieval, look-ahead rejection, transaction rollback,
contradictions, human resolution, audit-chain verification, Thai/English statement normalization,
provider contracts, and analysis hydration.

## Production boundary

Version `0.3.0` does not claim to include external OIDC federation, KMS-managed encryption,
PostgreSQL row-level security, WORM object storage, externally signed audit anchoring, distributed
rate limiting, broker/bank connectivity, or autonomous execution. Those controls remain required
before exposing CherryFin as a public financial service.

See [Security and Tenancy](docs/SECURITY_TENANCY.md) and
[Evidence Intelligence](docs/EVIDENCE_INTELLIGENCE.md).
