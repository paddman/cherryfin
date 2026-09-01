from __future__ import annotations

from cherryfin.intelligence.claims import ClaimLedger
from cherryfin.intelligence.models import (
    ClaimQueryResult,
    EvidenceIngestRequest,
    EvidenceIngestResult,
    KnowledgeQuery,
)
from cherryfin.intelligence.statements import FinancialStatementParser
from cherryfin.intelligence.store import SQLiteIntelligenceStore


class EvidencePipeline:
    """Ingests immutable evidence, extracts table facts, and records disagreements."""

    def __init__(
        self,
        store: SQLiteIntelligenceStore,
        *,
        ledger: ClaimLedger | None = None,
        statement_parser: FinancialStatementParser | None = None,
    ) -> None:
        self._store = store
        self._ledger = ledger or ClaimLedger(store)
        self._statement_parser = statement_parser or FinancialStatementParser()

    def ingest(self, request: EvidenceIngestRequest) -> EvidenceIngestResult:
        document, evidence_created = self._store.add_evidence(request.document)
        claims = []
        contradictions_by_id = {}
        statement_issues = []
        claims_created = 0

        if request.statement is not None:
            parsed = self._statement_parser.parse(
                table=request.statement,
                evidence=document.evidence,
            )
            statement_issues = parsed.issues
            for candidate in parsed.claims:
                result = self._ledger.record(candidate)
                claims.append(result.claim)
                claims_created += int(result.created)
                for contradiction in result.contradictions:
                    contradictions_by_id[contradiction.contradiction_id] = contradiction

        return EvidenceIngestResult(
            evidence=document.evidence,
            evidence_created=evidence_created,
            claims=claims,
            claims_created=claims_created,
            contradictions=list(contradictions_by_id.values()),
            statement_issues=statement_issues,
            snapshot_sha256=self._store.snapshot_sha256(),
        )

    def query(self, query: KnowledgeQuery) -> ClaimQueryResult:
        claims = self._store.query_claims(query)
        evidence_by_id = {}
        for claim in claims:
            for evidence_id in claim.evidence_ids:
                if evidence_id not in evidence_by_id:
                    evidence_by_id[evidence_id] = self._store.get_evidence(
                        evidence_id,
                        include_content=False,
                    ).evidence
        return ClaimQueryResult(
            query=query,
            claims=claims,
            evidence=list(evidence_by_id.values()),
            snapshot_sha256=self._store.snapshot_sha256(),
        )
