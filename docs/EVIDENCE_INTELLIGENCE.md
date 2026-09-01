# Evidence and Market Intelligence Architecture

## Objective

CherryFin must be able to answer four questions for every material financial statement:

1. What exactly is being claimed?
2. Which immutable source records support it?
3. When did the fact apply?
4. When did CherryFin first know it?

Without those answers, the system is a prose generator standing near a calculator.

## Core records

### Evidence

An evidence envelope contains a stable ID, source kind, source name, title, URI, retrieval time,
publication time, data time, trust score, content SHA-256, and license tag. The store accepts text,
binary content, or canonical structured payloads. Reusing an evidence ID with different immutable
content is rejected.

Source text is always untrusted data. It is never concatenated into a system prompt as executable
instruction.

### Financial claim

A claim contains:

- stable `claim_id`
- `subject_id` and normalized `predicate`
- typed decimal, text, boolean, or date value
- unit and currency
- reporting period
- `effective_at` business time
- `asserted_at` knowledge time
- supporting evidence IDs
- confidence, methodology, status, and revision lineage
- confidence bounded by the least-trusted supporting evidence

A claim cannot be stored until every evidence ID exists, and its `asserted_at` cannot precede the
supporting evidence's `observed_at`.

### Instrument

The instrument master maps a canonical UUID to one or more normalized identifiers such as ticker,
exchange symbol, ISIN, FIGI, CUSIP, SEDOL, LEI, or an internal ID. Identifier collisions fail
closed instead of silently merging two securities that happen to share a human-friendly symbol.

## Bitemporal semantics

CherryFin separates:

- **valid/business time**: when a fact applies
- **transaction/knowledge time**: when the system learned or changed its view of the fact

Claim status changes are append-recorded in `claim_status_history`. A later contradiction,
restatement, or human adjudication therefore does not rewrite the status visible in an earlier
replay.

Point-in-time retrieval applies both boundaries:

```text
claim.effective_at <= business_as_of
claim.asserted_at  <= knowledge_as_of
status event time  <= knowledge_as_of
```

This contract is mandatory for research evaluations and backtests.

## Statement normalization

The current parser accepts structured tables rather than pretending raw PDF text is reliable.
Rows contain a label and period-aware cells. The metric catalog recognizes common Thai and English
labels for revenue, profit, balance-sheet, cash-flow, debt, receivable, payable, EPS, and related
facts.

The parser:

- handles comma grouping, parentheses negatives, percentages, Thai digits, and null markers
- applies statement scales such as thousands or millions
- avoids multiplying per-share values by a statement-wide scale
- creates deterministic claim IDs
- preserves the source label and statement type
- emits unresolved-label and invalid-cell issues instead of guessing

Unknown labels remain unresolved until a reviewed taxonomy mapping is added.

## Contradiction lifecycle

Two claims are compared only when subject, predicate, and reporting context match. Numeric values
use configurable absolute and relative tolerances. Non-comparable currency or unit metadata is
flagged without inventing a conversion.

A detected disagreement:

1. creates a deterministic contradiction record
2. marks active claims disputed at the time the disagreement became known
3. caps model confidence when disputed claims are cited
4. remains open for a human reviewer
5. can later accept one claim, supersede the other, or dismiss the conflict

Explicit restatements use `supersedes_claim_id` and do not create a false contradiction.

## Provider boundary

Official-filing and licensed-market-data connectors map provider-specific records into the common
evidence and claim contracts. Provider adapters must preserve source IDs, timestamps, licensing,
and unmodified content hashes.

The HTTP filing primitive:

- requires HTTPS
- rejects credentials in URLs
- accepts only explicit allowlisted hosts
- rejects non-default ports
- disables redirects
- validates content type
- enforces a maximum byte count

It is intentionally not exposed as an arbitrary fetch endpoint.

## Model boundary

Before model inference, CherryFin may hydrate an `AnalysisRequest` from a point-in-time query. The
prompt receives structured claims and evidence envelopes. The answer must cite supplied IDs.

Deterministic policy blocks:

- invented evidence IDs
- invented claim IDs
- claims whose evidence was not supplied
- claims asserted before their evidence existed
- retracted claims
- look-ahead relative to requested business or knowledge time
- uncited high-confidence market analysis
- guaranteed-profit or risk-free language
- secret material
- side-effect and approval bypasses
- live execution through the analysis path

## Production migration

The SQLite implementation is the contract reference for a single process. A production deployment
should preserve behavior while adding:

- PostgreSQL row-level tenant isolation
- versioned object storage with retention lock for source content
- envelope encryption and managed keys
- service identity and scoped authorization
- signed ingestion manifests
- outbox/event delivery for downstream indexers
- independently verifiable audit logs
- provider license enforcement and entitlement checks
- point-in-time benchmark datasets and regression gates

No storage migration should collapse `effective_at`, `asserted_at`, and status-event time into one
column. That shortcut is cheap until someone asks why yesterday's backtest knew tomorrow's filing.
