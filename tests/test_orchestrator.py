from datetime import UTC, datetime
from typing import Any

import pytest

from cherryfin.agents.orchestrator import CherryFinancialAgent
from cherryfin.core.models import AgentMode, AnalysisRequest
from cherryfin.providers.llm import LLMResult
from cherryfin.settings import Settings


class FakeProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> LLMResult:
        assert "evidence-first" in system_prompt
        assert "UNTRUSTED_INPUT" in user_prompt
        return LLMResult(data=self.payload, model="fake")


def _payload(**updates: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "investment_research",
        "summary": "A scenario, not a current recommendation.",
        "key_findings": [],
        "assumptions": [],
        "calculations": [],
        "evidence_ids_used": [],
        "risks": [
            {
                "code": "market-risk",
                "level": "high",
                "message": "Prices can fall.",
                "mitigation": "Use position limits.",
            }
        ],
        "proposed_actions": [],
        "limitations": [],
        "confidence": 0.95,
        "confidence_reasons": ["Model estimate only."],
        "as_of": datetime.now(UTC).isoformat(),
    }
    payload.update(updates)
    return payload


@pytest.mark.asyncio
async def test_orchestrator_caps_uncited_market_confidence() -> None:
    agent = CherryFinancialAgent(provider=FakeProvider(_payload()), settings=Settings())

    response = await agent.analyze(
        AnalysisRequest(query="วิเคราะห์หุ้นนี้", mode=AgentMode.INVESTMENT_RESEARCH)
    )

    assert response.answer.confidence == 0.35
    assert response.policy.allowed is True
    assert response.evaluation.passed is False
    assert "No time-stamped market evidence was supplied." in response.answer.limitations


@pytest.mark.asyncio
async def test_orchestrator_blocks_fabricated_evidence_id() -> None:
    payload = _payload(evidence_ids_used=["made-up-source"], confidence=0.3)
    agent = CherryFinancialAgent(provider=FakeProvider(payload), settings=Settings())

    response = await agent.analyze(
        AnalysisRequest(query="Analyze a stock", mode=AgentMode.INVESTMENT_RESEARCH)
    )

    assert response.policy.allowed is False
    assert "fabricated_evidence_reference" in response.evaluation.critical_failures
