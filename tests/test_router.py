from cherryfin.agents.router import route_mode
from cherryfin.core.models import AgentMode


def test_routes_thai_personal_finance() -> None:
    assert route_mode("ช่วยวางแผนหนี้และเงินสำรองฉุกเฉิน", AgentMode.AUTO) is AgentMode.PERSONAL_CFO


def test_routes_portfolio_risk() -> None:
    assert route_mode("คำนวณ drawdown และความเสี่ยงพอร์ต", AgentMode.AUTO) is AgentMode.PORTFOLIO_RISK


def test_explicit_mode_wins() -> None:
    assert route_mode("tell me about a stock", AgentMode.BUSINESS_CFO) is AgentMode.BUSINESS_CFO
