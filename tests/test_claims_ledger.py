from datetime import UTC, datetime
from decimal import Decimal

from cherryfin.core.models import (
    ClaimStatus,
    ClaimValue,
    ClaimValueKind,
    Evidence,
    EvidenceKind,
    FinancialClaim,
)
from cherryfin.intelligence.claims import ClaimLedger, ContradictionDetector
from cherryfin.intelligence.models import ContradictionStatus, EvidenceDocument
from cherryfin.intelligence.store import SQLiteIntelligenceStore


def _add_evidence(store: SQLiteIntelligenceStore, evidence_id: str, day: int) -> None:
    store.add_evidence(
        EvidenceDocument(
            evidence=Evidence(
                evidence_id=evidence_id,
                kind=EvidenceKind.OFFICIAL_FILING,
                source_name=f"Official source {evidence_id}",
                title="Financial statements",
                observed_at=datetime(2026, 2, day, tzinfo=UTC),
                trust_score=0.98,
            ),
            content=f"content-{evidence_id}",
        )
    )


def _claim(
    claim_id: str,
    evidence_id: str,
    value: str,
    *,
    period_day: int = 31,
    supersedes_claim_id: str | None = None,
) -> FinancialClaim:
    return FinancialClaim(
        claim_id=claim_id,
        subject_id="issuer:abc",
        predicate="revenue",
        value=ClaimValue(
            kind=ClaimValueKind.DECIMAL,
            decimal_value=Decimal(value),
        ),
        unit="currency",
        currency="THB",
        period_start=datetime(2025, 1, 1, tzinfo=UTC),
        period_end=datetime(2025, 12, period_day, tzinfo=UTC),
        effective_at=datetime(2025, 12, period_day, tzinfo=UTC),
        asserted_at=datetime(2026, 2, 10, tzinfo=UTC),
        evidence_ids=[evidence_id],
        confidence=0.95,
        supersedes_claim_id=supersedes_claim_id,
    )


def test_material_numeric_contradiction_marks_claims_disputed() -> None:
    store = SQLiteIntelligenceStore()
    _add_evidence(store, "ev_one", 1)
    _add_evidence(store, "ev_two", 2)
    ledger = ClaimLedger(store)

    first = ledger.record(_claim("clm_one", "ev_one", "100"))
    second = ledger.record(_claim("clm_two", "ev_two", "125"))

    assert first.created is True
    assert len(second.contradictions) == 1
    assert second.contradictions[0].severity.value == "material"
    assert store.get_claim("clm_one").status is ClaimStatus.DISPUTED
    assert store.get_claim("clm_two").status is ClaimStatus.DISPUTED


def test_numeric_values_within_tolerance_do_not_conflict() -> None:
    store = SQLiteIntelligenceStore()
    _add_evidence(store, "ev_one", 1)
    _add_evidence(store, "ev_two", 2)
    detector = ContradictionDetector(
        relative_tolerance=Decimal("0.01"),
        absolute_tolerance=Decimal("0.01"),
    )
    ledger = ClaimLedger(store, detector)
    ledger.record(_claim("clm_one", "ev_one", "100"))
    second = ledger.record(_claim("clm_two", "ev_two", "100.50"))
    assert second.contradictions == []


def test_different_reporting_periods_do_not_conflict() -> None:
    store = SQLiteIntelligenceStore()
    _add_evidence(store, "ev_one", 1)
    _add_evidence(store, "ev_two", 2)
    ledger = ClaimLedger(store)
    ledger.record(_claim("clm_one", "ev_one", "100", period_day=30))
    second = ledger.record(_claim("clm_two", "ev_two", "125", period_day=31))
    assert second.contradictions == []


def test_explicit_revision_supersedes_without_false_contradiction() -> None:
    store = SQLiteIntelligenceStore()
    _add_evidence(store, "ev_one", 1)
    _add_evidence(store, "ev_two", 2)
    ledger = ClaimLedger(store)
    ledger.record(_claim("clm_one", "ev_one", "100"))
    revised = ledger.record(
        _claim(
            "clm_two",
            "ev_two",
            "125",
            supersedes_claim_id="clm_one",
        )
    )
    assert revised.contradictions == []
    assert store.get_claim("clm_one").status is ClaimStatus.SUPERSEDED
    assert store.get_claim("clm_two").status is ClaimStatus.ACTIVE


def test_human_resolution_accepts_one_claim_and_supersedes_the_other() -> None:
    store = SQLiteIntelligenceStore()
    _add_evidence(store, "ev_one", 1)
    _add_evidence(store, "ev_two", 2)
    ledger = ClaimLedger(store)
    ledger.record(_claim("clm_one", "ev_one", "100"))
    result = ledger.record(_claim("clm_two", "ev_two", "125"))
    contradiction = result.contradictions[0]

    resolved = store.resolve_contradiction(
        contradiction_id=contradiction.contradiction_id,
        accepted_claim_id="clm_two",
        resolution_note="Audited restatement accepted by a human reviewer.",
    )

    assert resolved.status is ContradictionStatus.RESOLVED
    assert resolved.accepted_claim_id == "clm_two"
    assert store.get_claim("clm_two").status is ClaimStatus.ACTIVE
    assert store.get_claim("clm_one").status is ClaimStatus.SUPERSEDED


def test_text_claim_mismatch_is_detected() -> None:
    detector = ContradictionDetector()
    base = FinancialClaim(
        claim_id="clm_text_one",
        subject_id="issuer:abc",
        predicate="auditor_opinion",
        value=ClaimValue(kind=ClaimValueKind.TEXT, text_value="Unqualified"),
        effective_at=datetime(2025, 12, 31, tzinfo=UTC),
        asserted_at=datetime(2026, 2, 1, tzinfo=UTC),
        evidence_ids=["ev_one"],
        confidence=0.9,
    )
    other = base.model_copy(
        update={
            "claim_id": "clm_text_two",
            "value": ClaimValue(kind=ClaimValueKind.TEXT, text_value="Qualified"),
            "evidence_ids": ["ev_two"],
        }
    )
    contradiction = detector.detect(base, other)
    assert contradiction is not None
    assert contradiction.severity.value == "warning"
