from __future__ import annotations

from cherryfin.core.models import AgentMode

_KEYWORDS: tuple[tuple[AgentMode, tuple[str, ...]], ...] = (
    (
        AgentMode.TRADING_RESEARCH,
        (
            "backtest",
            "paper trade",
            "trading strategy",
            "entry",
            "exit",
            "signal",
            "position sizing",
            "เทรด",
            "แบ็กเทสต์",
            "จุดเข้า",
            "จุดออก",
            "สัญญาณซื้อ",
            "สัญญาณขาย",
        ),
    ),
    (
        AgentMode.PORTFOLIO_RISK,
        (
            "portfolio",
            "allocation",
            "rebalance",
            "drawdown",
            "volatility",
            "var",
            "cvar",
            "sharpe",
            "พอร์ต",
            "จัดสรรสินทรัพย์",
            "ปรับสมดุล",
            "ความผันผวน",
            "ความเสี่ยงพอร์ต",
        ),
    ),
    (
        AgentMode.BUSINESS_CFO,
        (
            "runway",
            "ebitda",
            "accounts receivable",
            "accounts payable",
            "unit economics",
            "business cash flow",
            "บริษัท",
            "กระแสเงินสดธุรกิจ",
            "ลูกหนี้การค้า",
            "เจ้าหนี้การค้า",
            "ต้นทุนบริษัท",
            "งบการเงินบริษัท",
        ),
    ),
    (
        AgentMode.PERSONAL_CFO,
        (
            "budget",
            "salary",
            "expense",
            "debt",
            "mortgage",
            "retirement",
            "emergency fund",
            "งบประมาณ",
            "เงินเดือน",
            "รายรับ",
            "รายจ่าย",
            "หนี้",
            "ผ่อนบ้าน",
            "เกษียณ",
            "เงินสำรองฉุกเฉิน",
        ),
    ),
    (
        AgentMode.INVESTMENT_RESEARCH,
        (
            "stock",
            "bond",
            "etf",
            "fundamental",
            "valuation",
            "earnings",
            "filing",
            "หุ้น",
            "กองทุน",
            "พันธบัตร",
            "มูลค่าเหมาะสม",
            "งบการเงิน",
            "ผลประกอบการ",
        ),
    ),
)


def route_mode(query: str, requested_mode: AgentMode) -> AgentMode:
    if requested_mode is not AgentMode.AUTO:
        return requested_mode

    normalized = query.casefold()
    scores: dict[AgentMode, int] = {}
    for mode, keywords in _KEYWORDS:
        scores[mode] = sum(1 for keyword in keywords if keyword in normalized)

    best_mode = max(scores, key=scores.get)
    if scores[best_mode] == 0:
        return AgentMode.INVESTMENT_RESEARCH
    return best_mode
