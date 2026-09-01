# CherryFin

**Point-in-time financial intelligence. Evidence first. Human controlled.**

CherryFin is not designed as a confident finance chatbot. It is a financial-agent foundation that
separates source evidence, verified claims, deterministic calculations, model analysis, policy,
human approval, and execution.

Version `0.2.0` adds the evidence and market-intelligence layer required before serious investment,
CFO, portfolio, or trading research can be trusted.

## What is included

- Thai and English financial-agent routing
- Personal CFO, investment research, portfolio risk, business CFO, and trading-research modes
- OpenAI-compatible model adapter for LM Studio, Ollama, vLLM, SGLang, and approved hosted models
- Immutable evidence envelopes with SHA-256 integrity, timestamps, trust, and licensing metadata
- Text, structured JSON, XBRL/XML, and binary/PDF evidence storage
- Canonical instrument master with collision-safe identifiers
- Financial claims ledger with typed values and source lineage
- Bitemporal point-in-time retrieval using business time and knowledge time
- Contradiction detection, disputed status, explicit supersession, and human resolution primitives
- Thai/English table-aware statement-row normalization
- Official-filing connector contract with HTTPS allowlisting and byte limits
- Licensed market-data connector contract with point-in-time observations
- Deterministic growth, loan, and historical portfolio-risk calculations
- Policy checks for fabricated IDs, unsupported claims, look-ahead, stale data, guarantees, secrets,
  approval bypass, and transaction attempts
- FastAPI, SQLite persistence, Docker, tests, and read-only CI verification

## Why two clocks matter

Financial systems routinely fail by mixing what happened with what was known.

CherryFin records:

- `effective_at`: when a fact or market observation applies in the financial world
- `asserted_at`: when the claim entered the knowledge system
- `observed_at`: when the supporting evidence was retrieved or received
- `knowledge_as_of`: the latest information a replay or backtest is allowed to know
- `business_as_of`: the financial date being analyzed

A 2025 result published in February 2026 cannot be used in a backtest pretending to operate in
January 2026. Humans invented enough creative accounting already; the software need not add time
travel.

## Architecture

```text
Official filings / licensed market data / approved user documents
                              |
                              v
                 Integrity and source-policy checks
                              |
                              v
             Immutable evidence store + content SHA-256
                              |
                              v
        Table-aware extraction / provider-normalized observations
                              |
                              v
        Typed claims ledger + bitemporal status history
                              |
                 +------------+-------------+
                 |                          |
                 v                          v
       Contradiction detector      Point-in-time retrieval
                 |                          |
                 +------------+-------------+
                              v
              Cherry analysis context with claim/evidence IDs
                              |
                              v
                  LLM synthesis under deterministic policy
                              |
                              v
                    Quality gate and action proposals
                              |
                              v
              Separate human approval and execution services
```

The analysis API never authorizes a transaction. No bank, broker, payment, custody, or vault
credential is exposed to the model.

## Start locally

```bash
git switch feat/evidence-market-intelligence
cp .env.example .env
python -m pip install -e ".[dev]"
pytest
uvicorn cherryfin.api.main:app --host 0.0.0.0 --port 8080
```

Configure a local model when needed:

```dotenv
CHERRYFIN_LLM_BASE_URL=http://127.0.0.1:1234/v1
CHERRYFIN_LLM_API_KEY=local
CHERRYFIN_LLM_MODEL=qwen3.5-35b
```

For a persistent local intelligence store:

```dotenv
CHERRYFIN_INTELLIGENCE_STORE_PATH=./data/intelligence.db
```

Outside development or test, mutating APIs remain unavailable until an admin key is configured:

```dotenv
CHERRYFIN_ENVIRONMENT=production
CHERRYFIN_ADMIN_API_KEY=replace-with-a-secret-from-a-vault
```

Send that key only to mutating intelligence endpoints and raw evidence-content reads:

```text
X-CherryFin-Admin-Key: <secret>
```

Do not place the key in prompts, evidence text, source documents, logs, or repository files.

## Docker

```bash
docker compose up --build
```

The Compose configuration mounts a named volume at `/var/lib/cherryfin` and keeps the remaining
container filesystem read-only.

## Main APIs

### Health and capabilities

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/v1/capabilities
```

### Register an instrument

```bash
curl -X POST http://127.0.0.1:8080/v1/instruments \
  -H 'Content-Type: application/json' \
  -H 'X-CherryFin-Admin-Key: development-key' \
  -d '{
    "name": "Example Public Company",
    "asset_class": "equity",
    "currency": "THB",
    "exchange": "SET",
    "identifiers": [
      {"scheme": "ticker", "value": "ABC", "venue": "SET", "primary": true}
    ]
  }'
```

### Ingest official evidence and a structured statement table

```bash
curl -X POST http://127.0.0.1:8080/v1/evidence/ingest \
  -H 'Content-Type: application/json' \
  -H 'X-CherryFin-Admin-Key: development-key' \
  -d '{
    "document": {
      "evidence": {
        "evidence_id": "ev_annual_2025",
        "kind": "official_filing",
        "source_name": "Official Registry",
        "title": "2025 audited financial statements",
        "observed_at": "2026-02-15T09:00:00Z",
        "data_as_of": "2025-12-31T23:59:59Z",
        "trust_score": 0.98,
        "license_tag": "public-filing"
      },
      "content": "approved extracted text or canonical source payload",
      "mime_type": "text/plain",
      "language": "th"
    },
    "statement": {
      "subject_id": "issuer:abc",
      "statement_type": "income_statement",
      "currency": "THB",
      "scale": "millions",
      "rows": [
        {
          "label": "รายได้รวม",
          "cells": [
            {
              "period_start": "2025-01-01T00:00:00Z",
              "period_end": "2025-12-31T23:59:59Z",
              "raw_value": "1234.50"
            }
          ]
        }
      ]
    }
  }'
```

CherryFin computes the content hash, stores the evidence, converts recognized rows into claims,
records unresolved labels, and reports contradictions with existing claims.

### Query knowledge exactly as it existed

```bash
curl -X POST http://127.0.0.1:8080/v1/claims/query \
  -H 'Content-Type: application/json' \
  -d '{
    "subject_id": "issuer:abc",
    "predicate": "revenue",
    "business_as_of": "2025-12-31T23:59:59Z",
    "knowledge_as_of": "2026-02-20T00:00:00Z"
  }'
```

### Hydrate an analysis from the claims ledger

Include `knowledge_context` in `/v1/analyze`:

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

The API retrieves only eligible claims and their supporting evidence, attaches a snapshot digest,
and then calls the model. The model must return the exact `claim_ids_used` and `evidence_ids_used`.
Fabricated references fail policy and evaluation.

## Current boundaries

This milestone deliberately does **not** include:

- automatic crawling of arbitrary URLs
- a bundled commercial market-data feed
- broker, bank, exchange, custody, payment, or tax filing execution
- production identity, tenant isolation, KMS, WORM object storage, or distributed consensus
- PDF layout extraction or OCR as a trusted source of truth
- valuation promises, price predictions, or claims of profitable performance

The official-filing fetcher accepts only explicit HTTPS hosts, disables redirects, validates content
type, and enforces a byte limit. It is a connector primitive, not a public arbitrary-URL endpoint.

SQLite is suitable for the single-node foundation and deterministic tests. A production multi-node
deployment should retain these contracts while moving metadata to PostgreSQL, source objects to
versioned object storage, secrets to a vault, and audit records to tamper-evident retention.

## Tests

```bash
python -m compileall -q src tests
pytest
ruff check .
ruff format --check .
```

The suite covers calculations, routing, policy, evidence integrity, binary documents, identifier
collisions, claim dependencies, bitemporal retrieval, status replay, contradictions, revisions,
human resolution, Thai/English financial tables, provider contracts, and analysis hydration.

See [docs/EVIDENCE_INTELLIGENCE.md](docs/EVIDENCE_INTELLIGENCE.md) for the data model and security
invariants.
