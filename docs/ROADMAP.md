# CherryFin roadmap

The roadmap is ordered by trust, not by visual features. A polished dashboard must not arrive before the evidence and evaluation foundation it displays.

## Phase 0 — High-assurance foundation

**Implemented in the first foundation pull request**

- canonical request, evidence, calculation, risk, action, policy, and evaluation schemas;
- Thai/English mode routing;
- OpenAI-compatible model provider;
- deterministic compound-growth, loan, and portfolio-risk calculations;
- confidence cap for uncited market analysis;
- fabricated evidence-ID detection;
- market/news freshness warnings;
- credential preflight blocking;
- side-effect classification and transaction blocking;
- answer evaluation gate;
- FastAPI, Docker, CI, tests, and engineering rules.

Exit gate: unit tests pass and no analysis response can authorize execution.

## Phase 1 — Evidence-first research agent

Build the first genuinely useful Cherry workflow:

- official filing connectors and parser versioning;
- exchange/security master with ticker history and corporate actions;
- licensed or explicitly permitted market data connector interface;
- evidence store with hash and point-in-time timestamps;
- claims ledger;
- retrieval planner based on required claim types, not generic similarity alone;
- table-aware financial statement extraction;
- cross-period and cross-source verification;
- Thai/English research reports with exact evidence references;
- stale-data and contradictory-source user interface.

Evaluation set:

- revenue, margin, cash flow, debt, dilution, and segment questions;
- restatements and corrected filings;
- same ticker on different exchanges;
- malicious instructions embedded in a source;
- unavailable current price;
- conflicting media and company claims.

Exit gate: unsupported factual claim rate below an agreed threshold on a versioned blind set, with zero fabricated citations.

## Phase 2 — Personal CFO

- consented transaction import and normalized personal ledger;
- income/expense classification with user correction memory;
- monthly cash-flow forecast;
- emergency-fund and sinking-fund planner;
- debt avalanche/snowball scenarios with fees and refinancing costs;
- goal planning with inflation and uncertainty bands;
- anomaly and subscription detection;
- configurable country/tax rule modules without pretending to file taxes;
- explainable alerts and privacy controls.

Exit gate: every recommendation can be reproduced from ledger entries and assumptions; no account credential is stored outside the vault.

## Phase 3 — Portfolio and risk officer

- position ingestion and instrument normalization;
- exposure by asset, issuer, sector, geography, currency, duration, and factor;
- concentration and liquidity risk;
- historical and parametric VaR/CVaR with clear limitations;
- stress tests and named macro scenarios;
- tax-lot-aware rebalance simulation;
- fees, spreads, slippage, and turnover estimates;
- suitability profile and policy limits;
- proposed rebalance as a non-executable plan.

Exit gate: risk metrics match independent reference implementations within defined tolerances and every result records data/version lineage.

## Phase 4 — Business CFO

- double-entry business ledger and chart-of-accounts mapping;
- bank, invoice, AR/AP, payroll, and billing connector interfaces;
- cash runway and liquidity forecast;
- budget vs actual and driver-based forecasting;
- unit economics, cohort, margin, and working-capital analysis;
- scenario planning and variance explanations;
- anomaly detection with evidence;
- board and management report generator;
- approval workflow for exported or shared sensitive reports.

Exit gate: forecasts preserve reconciliation to source ledgers and scenario assumptions are versioned.

## Phase 5 — Trading research laboratory

This phase remains research and paper trading by default.

- point-in-time market data and universe builder;
- event and corporate-action handling;
- backtest engine with fees, spread, slippage, latency, and market calendars;
- walk-forward validation and embargoed time-series cross-validation;
- survivorship and leakage checks;
- strategy registry and experiment manifests;
- paper broker and shadow mode;
- portfolio-level risk limits;
- model/rule comparison with confidence intervals;
- kill criteria for degraded strategies.

Exit gate: no result can be promoted without reproducible manifests, out-of-sample tests, risk limits, and paper-trading evidence. Cherry must never market a backtest as guaranteed future performance.

## Phase 6 — Controlled execution, optional

Only pursue this phase after product, security, operational, and jurisdiction-specific review.

- separate execution service and credential vault;
- signed immutable action proposal;
- pre-trade preview;
- step-up authentication and explicit human approval;
- account, asset, amount, price, and time limits;
- idempotency and duplicate prevention;
- venue/broker status reconciliation;
- cancellation and compensation workflow;
- immutable receipt and audit trail;
- emergency kill switch;
- segregation of duties and periodic access review.

Exit gate: red-team attempts cannot expand proposal scope or bypass approval, and operational/legal owners sign off on the exact deployment behavior.

## Continuous workstreams

### Evaluation and red team

Maintain versioned suites for:

- financial arithmetic;
- document/table reasoning;
- evidence faithfulness;
- current-data freshness;
- suitability;
- Thai financial terminology;
- prompt injection and data poisoning;
- secret exfiltration;
- tool and side-effect policy;
- confidence calibration;
- latency and cost.

### Model strategy

Use model routing based on measured task performance, not brand preference. A small local model may classify and extract; a stronger model may synthesize; deterministic tools verify; a separate model or rule set may challenge high-impact claims. Every model/prompt update runs the same blind evaluation set before promotion.

### Product experience

Present evidence, assumptions, time, uncertainty, and alternatives in the main interface—not hidden behind a disclaimer. For every recommendation, the user should be able to answer:

1. What data did Cherry use?
2. How current is it?
3. What calculation or assumption drives the result?
4. What could make the result wrong?
5. What happens if I approve the proposed action?
