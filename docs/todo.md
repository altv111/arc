# ARC TODO

This is a living implementation backlog for design scenarios that are understood
but not yet built. Add to it as new operational cases appear; implement from it
when the shape is stable.

## Multi-SNAP / Follow-the-Sun Runs

- [x] Add `snapshot_id` to `DatasetResolver` fetch cache keys so same-day Japan
      and global SNAPs cannot reuse stale in-memory datasets.
- [ ] Model SNAP completion as the primary ARC entry event:
      `SnapCompleted(snapshot_id, business_date, run_type, population_scope,
      cep_event_id, reporting_cutoff_time, requested_rules)`.
- [x] Use separate V1 rule JSON files for snap populations:
      `row1_japan.json` and `row1_global.json`.
- [x] Keep scope only in JSON for V1; runtime event/human operator chooses which
      JSON to run.
- [ ] Include SNAP metadata in run/evidence records so users can distinguish
      Japan early snap from global snap.
- [x] Add tests for two same-day runs with different `snapshot_id` and different
      population scopes.
- [ ] Add demo snap runner CLI with explicit snap events:
      `--japan-snap-complete` and `--global-snap-complete`.
- [ ] Keep snap runner CLI demo-convenience only; keep snapshot/scope behavior
      as real platform code.
- [x] First implement generic CLI flags:
      `--rule`, `--fixtures`, and `--snapshot-id`.

## Rule and Scope Model

- [x] Treat `parent_scope` as rule-authored only for V1.
- [ ] Add validation for unsupported or misspelled hierarchy levels in scopes
      when reporting metadata is available.
- [ ] Decide whether gate `on_breach` supports bypass behavior such as
      `continue`, `skip_materiality`, or `go_to_decide`.

## Reporting Contracts

- [x] Add machine-readable dataset contract output, likely JSON, alongside the
      current terminal `--format datasets` view.
- [ ] Include expected field lists per dataset when handlers can declare them.
- [ ] Include runtime placeholders explicitly, for example
      `<upstream:missing_trade_ids>` and `<runtime:run_type>`.
- [ ] Add contract examples for Japan-only and global SNAP executions.

## Attribute Classifiers

- [ ] Enrich `attribute_missing_trades` with real evidence sources:
      RiskFinder calc status, Kannon presence, T-1 presence, history, and
      holiday calendars.
- [x] Add stub-backed attribution tool fixtures under a region/snap fixture root,
      for example `fixtures/demo/ECR/<snap>/2026-06-04/stubs/`.
- [x] Add `get_trade_calc_status(trade_ids)` backed by a CSV stub.
- [x] Add `check_historical_trade_status(trade_ids, lookback_days)` backed by a
      CSV stub.
- [x] Add `get_trade_presence_in_upstream(trade_ids)` backed by a CSV stub.
- [ ] Prefer already-fetched upstream evidence before new reporting calls:
      completeness exception rows from gate, Kannon trade sensi from evaluate,
      T-1 MVAR from evaluate, and ARC historical records.
- [ ] Rename/shape historical lookup as
      `check_historical_trade_status(trade_ids, lookback_days)` so it can use
      T-1 MVAR, yesterday's completeness exception report, or ARC records.
- [x] Teach `attribute_missing_trades` to classify at least:
      `arrived_but_errored`, `missing_in_upstream`, and
      `expected_to_arrive_late`.
- [x] Implement initial deterministic attribution policy:
      errored RiskFinder status -> `arrived_but_errored`; green RiskFinder
      status while missing -> defect; not-arrived + upstream present +
      historically late -> `expected_to_arrive_late`; not-arrived + upstream
      missing -> `missing_in_upstream`.
- [x] Include expected arrival time in `historical_trade_status.csv`; if the
      expected time is after the flash deadline, current run should hold and a
      future recheck lane should decide fill-from-live.
- [ ] Add `attribute_var_move` classifier for DoD or post-flash VaR movement
      root-cause classification.
- [ ] Add `attribute_sensi_diff` classifier for RF/Kannon sensitivity mismatch
      scenarios.
- [ ] Define canonical classifier output schema:
      root cause, confidence, evidence refs, late-arrival possibility, and
      operational follow-up.

## Decision Policy

- [x] Replace placeholder `always roll` logic with user-validated policy.
- [x] Define a simple real policy table, not demo-only branching, that maps
      attribution facts to correction decisions.
- [x] Support initial decisions:
      `roll`, `hold`, and `fill_from_live`.
- [x] For demo fixtures, make one breached portfolio resolve by waiting /
      `hold`, while the remaining material portfolios require `roll`.
- [x] Initial policy mappings:
      `arrived_but_errored -> roll`, `missing_in_upstream -> roll`,
      `expected_to_arrive_late -> hold`; `fill_from_live` belongs to future
      recheck after arrival is confirmed.
- [ ] Use root cause, late-arrival possibility, historical roll count, and
      available correction options.
- [ ] Implement breached-scope to correction-target mapping.
- [ ] Add policy evidence explaining why a decision was selected.
- [ ] Add tests for roll, fill-from-yesterday, hold/recheck, and escalate.

## Correction and Record

- [ ] Decide when shadow corrections become real correction client submissions.
- [ ] Add approval hooks if certain corrections require human confirmation.
- [ ] Enrich breach records with missing counts, material trade lists,
      classifier evidence, and decision rationale.
- [ ] Add stable record schema versioning for downstream consumers.

## Recheck Lane

- [ ] Design a future `recheck` lane for time-delayed outcomes such as trades
      expected to arrive late.
- [ ] Add `missing_trade_recheck` handler concept:
      original missing trades, prior attribution, prior correction action,
      current live/exception status -> resolved/still-missing trades.
- [ ] Add `decide_recheck_correction` concept:
      undo roll, fill from live, continue hold, or escalate.
- [ ] Ensure record artifacts preserve enough prior correction context for
      recheck handlers to know what was applied earlier in the day.

## Agentic / LangGraph Roadmap

- [ ] Keep ARC deterministic as the execution and audit spine.
- [ ] Prototype ReAct/LangGraph investigation loop for missing-trade
      attribution.
- [ ] Prototype ReAct/LangGraph commentary loop for post-flash VaR movement
      explanations.
- [ ] Define tool contracts for agentic loops:
      RiskFinder status, Kannon presence, historical rolls, holidays, incidents,
      market data moves, position changes, and prior commentary retrieval.
- [ ] Ensure agentic outputs are structured observations, not direct mutations.

## MD Demo Fixture Pack

- [x] Create realistic ECR demo fixture roots:
      `fixtures/demo/ECR/japan/2026-06-04/` and
      `fixtures/demo/ECR/global/2026-06-04/`.
- [x] Store full CSV copies under each snap root for demo clarity; Japan is a
      subset while global contains all portfolios.
- [x] Use `region=Japan` to distinguish Japan scope; do not use separate UBR8 or
      legal entity for this demo distinction.
- [x] Build `completeness_summary.csv` with 20 portfolios:
      15 clean portfolios and 5 breached portfolios.
- [x] For the 5 breached portfolios, model roughly 100 bad trades total:
      20 bad trades per breached portfolio out of 100 expected trades.
- [x] Build `completeness_exception_report.csv` with all bad trade IDs and full
      hierarchy context.
- [x] Build `tminus1_trade_mvar.csv` for the 100 bad trades.
- [x] Build `kannon_trade_level_sensi.csv` for the 100 bad trades.
- [x] Build `dod_var_extract__portfolio.csv` for the 5 breached portfolios and
      enough clean portfolios to demonstrate filtering.
- [x] Split the 100 bad trades across attribution outcomes:
      `arrived_but_errored`, `missing_in_upstream`, and
      `expected_to_arrive_late`.
- [x] Shape fixture outcomes so one breached portfolio produces `hold` or
      while the remaining material portfolios produce `roll`.
- [x] Add final record pretty-printer output for the demo:
      breached portfolios, material checks, attribution, decision, correction
      intent, and artifact path.
