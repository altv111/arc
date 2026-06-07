# ARC

ARC is a deterministic execution kernel for post-CEP risk controls.

The current implementation runs a row-based business DSL:

```text
parent_scope + check_scope + handler-specific filters
-> evaluate at check_grain
-> emit breached entities at breach_level
-> decide/act/record correction intent
```

Handlers remain pure compute components. Nodes and the runner own orchestration,
input resolution, idempotency, evidence, run-state persistence, and correction
client submission.

## Rule Flow

`fixtures/rules/row1.json` describes one rule family row:

- `parent_scope`: rule-wide universe, currently `ubr_level_8 = Europe Core Rates`
- `impact_checks`: gate/evaluate checks
- `rows`: independent check rows; each row becomes its own executable node
- `check_scope`: row-level filter
- `check_grain`: level at which the metric is evaluated
- `breach_level`: level emitted as breached
- `attribute_handler`, `decide_handler`, `act_handler`, `record_handler`: correction chain

When `build_rule_from_json(...)` runs, row1 becomes:

```text
gate:missing_trade_threshold_gate:0
evaluate:dod_var_move:0
evaluate:mvar:0
evaluate:mvar:1
attribute
decide
act
record
```

Each check row gets a stable `node_id` and `spec_slice`. The `spec_slice` is part
of the idempotency key, so threshold/config changes invalidate cached results.

## Execution Lanes

The runner executes lanes in order:

```text
gate -> evaluate -> attribute -> decide -> act -> record
```

Gate behavior is controlled by `evaluation_policy.gate_logic`:

- `run_evaluates_only_if_any_gate_breaches`: skip evaluates if all gates pass
- `run_evaluates_always`: always run evaluates

The correction chain runs only if at least one evaluate node fails.

## Node Responsibilities

Every node runs through the same orchestration shell:

- resolve declared handler inputs through `DatasetResolver`
- apply `parent_scope` centrally before handler execution
- inject upstream results when a handler declares `_upstream_results`
- compute idempotency key
- look up cached `NodeResult`
- run handler with timeout
- map indeterminate/error cases
- persist evidence payloads
- persist run state
- update the blackboard

Handlers do not read files, access clients, call databases, use clocks, or touch
the blackboard.

## Clients

ARC currently has protocol-based client boundaries:

- `ReportingClient`: dataset fetch interface
- `CSVReportingClient`: fixture-backed implementation
- `CorrectionClient`: correction submission interface
- `ShadowCorrectionClient`: deterministic no-mutation correction receipt client

The runner depends on `ReportingClient`. `ActNode` depends on `CorrectionClient`.
Handlers depend only on `ResolvedInputs`.

## Current Datasets

The CSV fixtures under `fixtures/ECR/2026-06-04/` include:

- `completeness_summary.csv`
- `dod_var_extract.csv`
- `mvar.csv`
- `rf_sensi.csv`
- `kannon_sensi.csv`

`completeness_summary` and `dod_var_extract` use business-shaped headers. Some
fixture rows include hierarchy enrichment fields so row-level scoping remains
testable in local CSV mode.

## Correction Flow

Correction is shadow-only today:

```text
attribute_completeness_drilldown
-> decide_correction
-> act_correction
-> ShadowCorrectionClient receipt
-> record_breach
```

`act_correction` remains pure. It emits correction intents. `ActNode` submits
those intents to the correction client and persists the resulting receipt as
evidence.

## Running Locally

Install dev dependencies:

```bash
python3 -m venv env
./env/bin/pip install -e '.[dev]'
```

Run tests and lint:

```bash
./env/bin/pytest -q
./env/bin/ruff check .
```

Run the sample BA rule:

```bash
./env/bin/python -m arc.cli.run_ba
```

Runtime artifacts are written under `.arc_runs/`, which is ignored by git.
