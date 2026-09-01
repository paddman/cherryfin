from __future__ import annotations

from decimal import Decimal
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from cherryfin import __version__
from cherryfin.agents.orchestrator import (
    AgentOutputError,
    CherryFinancialAgent,
    UnsafeRequestError,
)
from cherryfin.core.models import AnalysisRequest, AnalysisResponse
from cherryfin.providers.llm import LLMProviderError, OpenAICompatibleProvider
from cherryfin.settings import Settings
from cherryfin.tools.calculators import (
    CompoundGrowthResult,
    LoanResult,
    PortfolioRiskResult,
    calculate_compound_growth,
    calculate_loan,
    calculate_portfolio_risk,
)

settings = Settings()
app = FastAPI(
    title="CherryFin API",
    version=__version__,
    description="Evidence-first financial intelligence with deterministic safety controls.",
)


def _build_agent() -> CherryFinancialAgent | None:
    if not settings.llm_model.strip():
        return None
    provider = OpenAICompatibleProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    return CherryFinancialAgent(provider=provider, settings=settings)


agent = _build_agent()


class CompoundGrowthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Decimal = Field(ge=0)
    annual_rate_pct: Decimal = Field(gt=-100)
    years: int = Field(ge=0, le=200)
    compounds_per_year: int = Field(default=12, ge=1, le=365)
    periodic_contribution: Decimal = Field(default=Decimal("0"), ge=0)
    contribution_at_beginning: bool = False


class LoanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Decimal = Field(gt=0)
    annual_rate_pct: Decimal = Field(ge=0, le=1000)
    term_months: int = Field(gt=0, le=1200)


class PortfolioRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periodic_returns: Annotated[list[float], Field(min_length=2, max_length=1_000_000)]
    periods_per_year: int = Field(default=252, gt=0, le=100_000)
    annual_risk_free_rate: float = Field(default=0.0, gt=-1)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "CherryFin",
        "tagline": "Financial intelligence. Evidence first. Human controlled.",
        "version": __version__,
    }


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "version": __version__,
        "llm_configured": agent is not None,
        "live_execution_enabled": settings.execution_enabled,
    }


@app.get("/v1/capabilities")
def capabilities() -> dict[str, object]:
    return {
        "modes": [
            "personal_cfo",
            "investment_research",
            "portfolio_risk",
            "business_cfo",
            "trading_research",
        ],
        "deterministic_calculators": [
            "compound_growth",
            "loan_amortization_summary",
            "historical_portfolio_risk",
        ],
        "execution": {
            "available": False,
            "reason": (
                "Analysis API never authorizes transactions; approval and execution are separate."
            ),
        },
    }


@app.post("/v1/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest) -> AnalysisResponse:
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail="Set CHERRYFIN_LLM_MODEL to enable the analysis agent.",
        )
    try:
        return await agent.analyze(request)
    except UnsafeRequestError as exc:
        raise HTTPException(status_code=400, detail=list(exc.reasons)) from exc
    except (LLMProviderError, AgentOutputError) as exc:
        raise HTTPException(
            status_code=502,
            detail="The configured model could not produce a valid structured answer.",
        ) from exc


@app.post("/v1/calculators/compound-growth", response_model=CompoundGrowthResult)
def compound_growth(request: CompoundGrowthRequest) -> CompoundGrowthResult:
    return calculate_compound_growth(**request.model_dump())


@app.post("/v1/calculators/loan", response_model=LoanResult)
def loan(request: LoanRequest) -> LoanResult:
    return calculate_loan(**request.model_dump())


@app.post("/v1/calculators/portfolio-risk", response_model=PortfolioRiskResult)
def portfolio_risk(request: PortfolioRiskRequest) -> PortfolioRiskResult:
    return calculate_portfolio_risk(**request.model_dump())


def run() -> None:
    uvicorn.run("cherryfin.api.main:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    run()
