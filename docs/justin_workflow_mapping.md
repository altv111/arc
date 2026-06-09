# Justin Workflow Mapping

This document maps the manual completeness workflow described by Justin to the
current ARC row1 execution graph, and sketches the future agentic investigation
loops for attribution, decisioning, and post-flash commentary.

## Big Picture

ARC treats the pre-flash and post-flash work as one continuous control process:

```text
find breached or risky data conditions
-> assess whether the condition is material
-> classify why it happened
-> decide whether it can be corrected
-> apply or recommend the correction
-> explain residual genuine VaR movement
```

The operational split between pre-flash and post-flash is useful for people and
timing, but not for the platform. The same lane model can support both:

```text
gate -> evaluate -> attribute -> decide -> act -> record/commentary
```

Pre-flash focuses on identifying fixable breaches and producing correction
intents. Post-flash focuses on explaining genuine, non-correctable VaR movement.

## Row1 Runtime Graph

For `fixtures/rules/row1.json`, the current graph is:

```text
gate:missing_trade_threshold_gate:0
  -> evaluate:dod_var_move:0 -----------------------------.
  -> evaluate:trade_level_tminus1_mvar_mat:0 --------------+-> attribute:attribute_missing_trades:0
  -> evaluate:trade_level_kannon_sensi_mat:0 --------------'
attribute:attribute_missing_trades:0
  -> decide
  -> act
  -> record
```

The reporting contract can be printed with:

```bash
./env/bin/python -m arc.cli.visualize_rule fixtures/rules/row1.json --format datasets
```

## Justin Story to ARC Mapping

| Justin manual step | ARC lane | Current node / handler | Current datasets | Current output |
| --- | --- | --- | --- | --- |
| Generate completeness summary for `ubr_level_8 = Europe Core Rates` | gate | `gate:missing_trade_threshold_gate:0` / `missing_trade_threshold_gate` | `completeness_summary`, `completeness_exception_report` | Breached portfolios, missing trade IDs, missing-trade hierarchy |
| Identify 3 portfolios with significant 1DVaR missing trades | gate | `missing_trade_threshold_gate` | Same as above | `breached_scopes` at `portfolio`; `missing_trade_ids` |
| Check DoD VaR move for those portfolios | evaluate | `evaluate:dod_var_move:0` / `dod_var_move` | `dod_var_extract` scoped by upstream breached portfolios | Pass/fail by portfolio; row1 fixture currently passes |
| Obtain missing trade IDs only for those portfolios | gate output reused by downstream | `missing_trade_threshold_gate` | `completeness_exception_report` | `missing_trades` downstream hint |
| Generate T-1 MVAR for missing trades | evaluate | `evaluate:trade_level_tminus1_mvar_mat:0` / `trade_level_tminus1_mvar_mat` | `tminus1_trade_mvar` scoped by upstream missing trade IDs | Material missing trades; breached portfolio |
| Generate trade-level Kannon sensi for missing trades | evaluate | `evaluate:trade_level_kannon_sensi_mat:0` / `trade_level_kannon_sensi_mat` | `kannon_trade_level_sensi` scoped by upstream missing trade IDs, risk type, sensitivity type | Material Kannon trades; breached portfolios |
| Mark portfolio1 and portfolio2 as breached | evaluate + attribute | Materiality handlers, then `attribute_missing_trades` | Upstream results | Attributed material portfolios |
| Document missing counts and material trade observations | record | `record` / `record_breach` | Upstream results and evidence refs | Canonical breach record |
| Classify root cause using RiskFinder/Kannon/T-1/history/holiday checks | attribute | `attribute:attribute_missing_trades:0` / `attribute_missing_trades` | Currently upstream only; future datasets listed below | Current lightweight `riskfinder_error` classification from exception status |
| Decide correction action | decide | `decide` / `decide_correction` | Upstream attribution | Temporary placeholder: always `roll` |
| Roll portfolio1 and portfolio2 | act | `act` / `act_correction` | Upstream decisions | Shadow correction intents and receipt |

## Current Gaps by Lane

### Gate

The gate lane is structurally aligned with Justin's workflow.

Current behavior:

- applies `parent_scope` centrally
- applies row-level `check_scope`
- evaluates missing percentage by `check_grain`
- emits `breach_level`
- emits missing trades for downstream checks

Future detail:

- support explicit bypass policies such as "if missing count is huge, bypass
  materiality and go straight to correction."

### Evaluate

The evaluate lane is now close to Justin's workflow.

Current behavior:

- DoD VaR checks only the portfolios discovered by the gate
- T-1 MVAR checks only missing trades
- Kannon sensi checks only missing trades and configured risk/sensitivity types

Future detail:

- add richer MVAR schema if the reporting team provides a business-shaped
  extract
- add RF-vs-Kannon sensitivity diff checks as separate evaluate rows

### Attribute

The attribute lane now has the right shape but not enough facts.

Current behavior:

- `attribute_missing_trades` groups missing/material trades by portfolio
- classifies simple statuses such as `riskfinder_error`
- emits attributed breaches for material portfolios

Future detail:

- add fact-rich classifiers:
  - `attribute_missing_trades`
  - `attribute_var_move`
  - `attribute_sensi_diff`
- make each classifier gather evidence from multiple tools/datasets

### Decide

The decide lane has the right convergence shape.

Current behavior:

- consumes all attribute classifier outputs
- emits correction decisions
- temporary placeholder policy always emits `roll`

Future detail:

- replace placeholder with user-validated policy:
  - roll is preferred for terminal RiskFinder errors
  - fill from yesterday may be allowed for specific root causes
  - escalate or hold/recheck if late arrival is still plausible
  - avoid repeated continuous rolls beyond agreed thresholds

### Act and Record

Act and record are deterministic and auditable.

Current behavior:

- `act_correction` emits correction intents
- `ActNode` submits them to `ShadowCorrectionClient`
- `record_breach` writes canonical evidence

Future detail:

- route shadow intents to real correction clients
- add user approval checkpoints if required
- enrich the record with final policy rationale

## Future ReAct / LangGraph Use Cases

The core ARC graph should remain deterministic. ReAct-style workflows are useful
as investigation loops around fact gathering and explanation, especially where
the next best tool depends on the previous observation.

The proposed boundary is:

```text
deterministic ARC node
-> calls an agentic investigation subgraph when configured
-> subgraph returns structured facts + evidence refs
-> ARC node validates and records those facts
```

The agentic loop should not directly mutate corrections or make unreviewed final
policy decisions. It should gather evidence, propose classifications, and write
structured observations that deterministic handlers can consume.

## Pre-Flash Agentic Use Case: Attribute Missing Trades

Goal: classify why material missing trades happened.

Possible LangGraph/ReAct state:

```text
missing_trade_ids
material_trade_ids
breached_portfolios
observations
candidate_root_causes
required_evidence
confidence
```

Possible tools:

- `get_riskfinder_calc_status(trade_ids)`
  - tells whether trades failed in RiskFinder, never arrived, or are still
    pending
- `get_completeness_exception_report(trade_ids)`
  - confirms missing statuses and hierarchy
- `get_kannon_trade_presence(trade_ids)`
  - tells whether the same trades are present in Kannon
- `get_tminus1_trade_mvar(trade_ids)`
  - confirms whether the trades existed yesterday and had material risk
- `get_historical_roll_records(portfolios, lookback_days)`
  - checks continuous roll count
- `get_historical_missing_trade_records(trade_ids, portfolios, lookback_days)`
  - checks whether this is recurring
- `get_regional_holiday_calendar(region, date)`
  - checks holiday candidate root causes
- `search_prior_incidents(portfolios, systems, date_range)`
  - retrieves similar operational incidents

Expected structured output:

```json
{
  "classifier": "missing_trades",
  "portfolio": "Portfolio Rates Linear",
  "root_cause": "riskfinder_error",
  "late_arrival_possible": false,
  "seen_in_kannon": true,
  "seen_t_minus_1": true,
  "continuous_roll_days": 1,
  "holiday_candidate": false,
  "evidence": []
}
```

## Pre-Flash Agentic Use Case: Decide Correction

Goal: choose a correction action from attributed facts and policy.

Possible tools:

- `get_decision_policy(rule_id, root_cause, breach_type)`
- `get_historical_roll_records(portfolios, lookback_days)`
- `get_available_correction_options(scope)`
- `simulate_correction_impact(option, scope)`
- `check_approval_requirement(option, scope, materiality)`

Expected structured output:

```json
{
  "decision": "roll",
  "target_scope": {
    "portfolio": ["Portfolio Rates Linear"]
  },
  "rationale": [
    "root cause is terminal RiskFinder error",
    "late arrival is not plausible",
    "continuous roll count is within policy"
  ],
  "requires_approval": false
}
```

Decisioning is more policy-sensitive than attribution. For that reason, the
first production version should probably be deterministic policy code, with an
agentic assistant used for explanation and exception analysis.

## Post-Flash Agentic Use Case: VaR Move Commentary

Post-flash is the strongest use case for agentic AI because it is exploratory
and explanatory. The goal shifts from "can we fix this breach?" to "why did VaR
move, and is the movement genuine?"

Possible tools:

- `get_flash_var_by_portfolio(date, run_type)`
- `get_post_flash_var_by_portfolio(date, run_type)`
- `get_var_component_breakdown(portfolio, date)`
- `get_sensi_diff(portfolio, risk_type, date)`
- `get_market_data_moves(date, risk_factors)`
- `get_position_changes(portfolio, date)`
- `get_trade_activity(portfolio, date)`
- `get_model_or_methodology_changes(date)`
- `get_known_incidents(date, systems)`
- `retrieve_prior_commentary(portfolio, risk_type, date_range)`

Possible ReAct loop:

```text
observe VaR movement
-> identify top contributing portfolios/risk factors
-> check if pre-flash breaches/corrections explain movement
-> inspect market data and position changes
-> compare to historical commentary
-> draft explanation
-> cite evidence
-> mark residual movement as genuine or unresolved
```

Expected structured output:

```json
{
  "portfolio": "Portfolio Rates Linear",
  "var_move": 1250000,
  "classification": "genuine_market_move",
  "drivers": [
    "EUR rates curve move",
    "position increase in long-end swaps"
  ],
  "pre_flash_linkage": {
    "had_breach": true,
    "correction_applied": true,
    "residual_move_after_correction": 900000
  },
  "commentary": "VaR increased primarily due to...",
  "evidence": []
}
```

## Platform Direction

The leadership direction fits ARC's architecture:

- build deterministic lanes and audit boundaries in pre-flash
- add agentic investigation loops where exploration is valuable
- reuse the same tool contracts, evidence store, and lane model in post-flash
- keep mutation and final policy actions deterministic and auditable

In short:

```text
ARC is the deterministic spine.
LangGraph/ReAct can become the exploratory nervous system.
```
