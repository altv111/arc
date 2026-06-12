from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import arc.handlers  # noqa: F401 - import-time registration
from arc.clients.reporting import CSVReportingClient
from arc.core.context import BARunContext
from arc.core.evidence_store import EvidenceStore
from arc.core.run_state import RunStateStore
from arc.handlers.registry import HANDLERS
from arc.nodes.base import compute_idempotency_key
from arc.rule import build_rule, build_rule_from_json
from arc.run_summary import render_run_summary
from arc.runner import Runner
from arc.visualize import (
    dataset_contract,
    dataset_contract_payload,
    render_dataset_contract,
    render_rule_mermaid,
    render_rule_plan,
    render_rule_rich,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _stores(tmp_path):
    return EvidenceStore(tmp_path / "evidence"), RunStateStore(tmp_path / "state")


def _ctx() -> BARunContext:
    return BARunContext(
        ba="ECR",
        business_date=date(2026, 6, 4),
        run_type="1dvar",
        snapshot_id="fixture-2026-06-04",
        config_version="test-config",
        code_version="test-code",
    )


def test_rule_builder_expands_one_node_per_check_row(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)

    rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )

    assert [node.node_id for node in rule.nodes] == [
        "gate:missing_trade_threshold_gate:0",
        "evaluate:dod_var_move:0",
        "evaluate:trade_level_tminus1_mvar_mat:0",
        "evaluate:trade_level_kannon_sensi_mat:0",
        "attribute:attribute_missing_trades:0",
        "decide",
        "act",
        "record",
    ]


def test_rule_visualization_renders_swimlane_paths(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )

    rendered = render_rule_mermaid(rule)

    assert "subgraph gate[gate]" in rendered
    assert "subgraph evaluate[evaluate]" in rendered
    assert "subgraph attribute[attribute]" in rendered
    assert "n_gate_missing_trade_threshold_gate_0 --> n_evaluate_dod_var_move_0" in rendered
    assert "n_evaluate_trade_level_tminus1_mvar_mat_0 --> n_attribute_attribute_missing_trades_0" in rendered
    assert "n_attribute_attribute_missing_trades_0 --> n_decide" in rendered
    assert "n_decide --> n_act" in rendered
    assert "n_act --> n_record" in rendered


def test_rule_plan_view_includes_datasets_and_decision_path(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )

    rendered = render_rule_plan(rule)

    assert "ARC Plan | Rule 1: Completeness" in rendered
    assert "Parent scope: ubr_level_8=Europe Core Rates" in rendered
    assert "completeness_summary" in rendered
    assert "completeness_exception_report" in rendered
    assert "dod_var_extract(grain=portfolio; var_type=TOTAL; fields=<runtime_curr_var>,<runtime_prev_var>; portfolio_names=<upstream:gate_breached_portfolios>)" in rendered
    assert "tminus1_trade_mvar(trade_ids=<upstream:missing_trade_ids>)" in rendered
    assert "kannon_trade_level_sensi(trade_ids=<upstream:missing_trade_ids>; risk_type_list=abc; sensitivity_type_list=Delta)" in rendered
    assert "emits      : correction decisions" in rendered
    assert "emits      : correction intents + correction receipt" in rendered
    assert "Dataset contract" in rendered
    assert "- completeness_summary" in rendered
    assert "- tminus1_trade_mvar" in rendered


def test_rule_rich_view_includes_ansi_demo_plan(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )

    rendered = render_rule_rich(rule)

    assert "\x1b[" in rendered
    assert "ARC Rule Plan | Rule 1: Completeness" in rendered
    assert "gate:missing_trade_threshold_gate:0" in rendered
    assert "attribute:attribute_missing_trades:0" in rendered
    assert "dod_var_extract" in rendered
    assert "Compute and decision paths" in rendered


def test_dataset_contract_lists_reporting_requirements(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )

    contract = dataset_contract(rule)
    rendered = render_dataset_contract(rule)

    assert [entry["dataset"] for entry in contract] == [
        "completeness_exception_report",
        "completeness_summary",
        "dod_var_extract",
        "historical_trade_status",
        "kannon_trade_level_sensi",
        "riskfinder_calc_status",
        "tminus1_trade_mvar",
        "upstream_trade_presence",
    ]
    assert "_upstream_results" not in rendered
    assert "parent_scope is applied centrally" in rendered
    assert "portfolio_names=<upstream:gate_breached_portfolios>" in rendered
    assert "trade_ids=<upstream:missing_trade_ids>" in rendered

    payload = dataset_contract_payload(rule)
    assert payload["rule_id"] == 1
    assert payload["parent_scope"] == {"ubr_level_8": ["Europe Core Rates"]}
    assert payload["datasets"][0]["dataset"] == "completeness_exception_report"
    assert {
        entry["dataset"]
        for entry in payload["datasets"]
    } >= {"riskfinder_calc_status", "historical_trade_status", "upstream_trade_presence"}


def test_check_grain_mismatch_fails_fast(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    spec = {
        "row_id": 1,
        "parent_scope": {"ubr_level_8": ["Europe Core Rates"]},
        "impact_checks": [
            {
                "check_id": "mvar",
                "mode": "gate",
                "check_grain": "trade",
                "rows": [{"check_scope": {}, "breach_level": "trade", "threshold": {"rel": 1}}],
            }
        ],
    }

    with pytest.raises(ValueError, match="check_grain"):
        build_rule(spec, evidence_store=evidence_store, run_state_store=run_state_store)


def test_row1_run_applies_parent_and_check_scopes(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    fixtures = REPO_ROOT / "fixtures" / "ECR" / "2026-06-04"
    rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )
    runner = Runner(
        reporting_client=CSVReportingClient(fixtures),
        evidence_store=evidence_store,
        run_state_store=run_state_store,
        runs_root=tmp_path / "runs",
    )

    report = runner.run_rule(rule, _ctx(), run_id="row1-test")

    assert report.status.value == "fail"
    assert report.artifacts_dir == tmp_path / "runs" / "row1-test"

    gate = next(result for result in report.results if result.node_id == "gate:missing_trade_threshold_gate:0")
    assert gate.metrics["missing_count"] == 5
    assert gate.metrics["total_count"] == 190
    assert len(gate.breached_scopes) == 3
    assert gate.downstream_hints["missing_trade_ids"] == ["T10", "T11", "T2", "T3", "T7"]
    assert all(
        scope.levels["ubr_level_8"] == ("Europe Core Rates",)
        for scope in gate.breached_scopes
    )

    dod = next(result for result in report.results if result.node_id == "evaluate:dod_var_move:0")
    assert dod.status.value == "pass"
    assert dod.metrics["n_scopes_examined"] == 3
    assert dod.metrics["n_breached_scopes"] == 0
    assert dod.upstream_data_versions.keys() == {"dod_var_extract"}

    mvar = next(
        result
        for result in report.results
        if result.node_id == "evaluate:trade_level_tminus1_mvar_mat:0"
    )
    assert mvar.status.value == "fail"
    assert mvar.metrics["n_missing_trades"] == 5
    assert mvar.metrics["n_material_trades"] == 1
    assert mvar.downstream_hints["material_trade_ids"] == ["T2"]
    assert mvar.breached_scopes[0].levels["portfolio"] == ("Portfolio Rates Linear",)

    kannon = next(
        result
        for result in report.results
        if result.node_id == "evaluate:trade_level_kannon_sensi_mat:0"
    )
    assert kannon.status.value == "fail"
    assert kannon.metrics["n_missing_trades"] == 5
    assert kannon.metrics["n_material_trades"] == 2
    assert kannon.downstream_hints["material_trade_ids"] == ["T2", "T3"]
    assert {scope.levels["portfolio"][0] for scope in kannon.breached_scopes} == {
        "Portfolio Rates Linear",
        "Portfolio Rates Options",
    }

    attribute = next(result for result in report.results if result.node_id == "attribute:attribute_missing_trades:0")
    assert attribute.metrics["n_portfolios_classified"] == 3
    assert attribute.metrics["n_material_portfolios"] == 2
    assert {
        row["portfolio"]: row["root_cause"]
        for row in attribute.downstream_hints["classifications"]
    } == {
        "Portfolio Rates Basis": "expected_to_arrive_late",
        "Portfolio Rates Linear": "arrived_but_errored",
        "Portfolio Rates Options": "arrived_but_errored",
    }

    record = next(result for result in report.results if result.node_id == "record")
    assert record.metrics["n_correction_intents"] == 2
    act = next(result for result in report.results if result.node_id == "act")
    assert act.metrics["n_correction_receipts"] == 1
    assert act.downstream_hints["correction_receipt"]["client"] == "ShadowCorrectionClient"
    assert act.downstream_hints["correction_receipt"]["mutation_performed"] is False
    assert act.downstream_hints["correction_receipt"]["n_intents"] == 2
    assert (tmp_path / "runs" / "row1-test" / "run.json").exists()


def test_demo_japan_and_global_snaps_have_distinct_scope_and_policy(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    runner_root = tmp_path / "runs"

    japan_rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1_japan.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )
    japan_runner = Runner(
        reporting_client=CSVReportingClient(REPO_ROOT / "fixtures" / "demo" / "ECR" / "japan" / "2026-06-04"),
        evidence_store=evidence_store,
        run_state_store=run_state_store,
        runs_root=runner_root,
    )
    japan_report = japan_runner.run_rule(
        japan_rule,
        _ctx().model_copy(update={"snapshot_id": "ECR-JAPAN-SNAP-2026-06-04"}),
        run_id="japan-demo",
    )

    global_rule = build_rule_from_json(
        REPO_ROOT / "fixtures" / "rules" / "row1_global.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )
    global_runner = Runner(
        reporting_client=CSVReportingClient(REPO_ROOT / "fixtures" / "demo" / "ECR" / "global" / "2026-06-04"),
        evidence_store=evidence_store,
        run_state_store=run_state_store,
        runs_root=runner_root,
    )
    global_report = global_runner.run_rule(
        global_rule,
        _ctx().model_copy(update={"snapshot_id": "ECR-GLOBAL-SNAP-2026-06-04"}),
        run_id="global-demo",
    )

    japan_gate = next(r for r in japan_report.results if r.node_id == "gate:missing_trade_threshold_gate:0")
    global_gate = next(r for r in global_report.results if r.node_id == "gate:missing_trade_threshold_gate:0")
    assert len(japan_gate.breached_scopes) == 2
    assert len(global_gate.breached_scopes) == 5

    japan_decide = next(r for r in japan_report.results if r.node_id == "decide")
    global_decide = next(r for r in global_report.results if r.node_id == "decide")
    assert [(d["decision"], d["classification"]) for d in japan_decide.downstream_hints["decisions"]] == [
        ("roll", "arrived_but_errored"),
        ("hold", "expected_to_arrive_late"),
    ]
    assert [(d["decision"], d["classification"]) for d in global_decide.downstream_hints["decisions"]] == [
        ("roll", "arrived_but_errored"),
        ("hold", "expected_to_arrive_late"),
        ("roll", "missing_in_upstream"),
        ("roll", "arrived_but_errored"),
        ("roll", "missing_in_upstream"),
    ]

    summary = render_run_summary(japan_report)
    assert "ARC Run Summary | Rule 1" in summary
    assert "Snapshot    : ECR-JAPAN-SNAP-2026-06-04" in summary
    assert "ECR Portfolio 01: arrived_but_errored -> roll" in summary
    assert "ECR Portfolio 02: expected_to_arrive_late -> hold" in summary
    assert str(runner_root / "japan-demo") in summary


def test_dod_var_move_can_plan_grain_specific_extract(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    spec = {
        "row_id": 2,
        "parent_scope": {"ubr_level_8": ["Europe Core Rates"]},
        "impact_checks": [
            {
                "check_id": "missing_trade_threshold_gate",
                "mode": "gate",
                "check_grain": "portfolio",
                "rows": [
                    {
                        "check_scope": {},
                        "breach_level": "portfolio",
                        "threshold": {"rel": 0},
                    }
                ],
            },
            {
                "check_id": "dod_var_move",
                "mode": "evaluate",
                "check_grain": "ubr_level_9",
                "rows": [
                    {
                        "check_scope": {},
                        "breach_level": "ubr_level_9",
                        "threshold": {"rel": 0.15},
                    }
                ],
            },
        ],
        "attribute_handler": "attribute_completeness_drilldown",
        "decide_handler": "decide_correction",
        "act_handler": "act_correction",
        "record_handler": "record_breach",
        "decision_options": ["fill_from_yesterday"],
    }
    rule = build_rule(spec, evidence_store=evidence_store, run_state_store=run_state_store)
    runner = Runner(
        reporting_client=CSVReportingClient(REPO_ROOT / "fixtures" / "ECR" / "2026-06-04"),
        evidence_store=evidence_store,
        run_state_store=run_state_store,
        runs_root=tmp_path / "runs",
    )

    report = runner.run_rule(rule, _ctx(), run_id="dod-ubr9")

    dod = next(result for result in report.results if result.node_id == "evaluate:dod_var_move:0")
    assert dod.status.value == "fail"
    assert dod.metrics["n_scopes_examined"] == 2
    assert all("ubr_level_9" in scope.levels for scope in dod.breached_scopes)
    assert dod.upstream_data_versions.keys() == {"dod_var_extract"}


def test_idempotency_changes_when_row_spec_changes(tmp_path):
    evidence_store, run_state_store = _stores(tmp_path)
    base_spec = {
        "row_id": 1,
        "parent_scope": {"ubr_level_8": ["Europe Core Rates"]},
        "impact_checks": [
            {
                "check_id": "mvar",
                "mode": "gate",
                "check_grain": "portfolio",
                "rows": [{"check_scope": {}, "breach_level": "portfolio", "threshold": {"rel": 5}}],
            }
        ],
    }
    changed_spec = {
        **base_spec,
        "impact_checks": [
            {
                **base_spec["impact_checks"][0],
                "rows": [{"check_scope": {}, "breach_level": "portfolio", "threshold": {"rel": 6}}],
            }
        ],
    }

    first = build_rule(base_spec, evidence_store=evidence_store, run_state_store=run_state_store)
    second = build_rule(changed_spec, evidence_store=evidence_store, run_state_store=run_state_store)

    first_key = compute_idempotency_key(
        rule_id=1,
        node_id=first.nodes[0].node_id,
        check_id=first.nodes[0].check_id,
        scope_hash="scope",
        config_version="config",
        code_version="code",
        runtime_identity={
            "ba": "ECR",
            "business_date": "2026-06-04",
            "run_type": "1dvar",
            "snapshot_id": "fixture",
        },
        handler_version=HANDLERS["mvar"].handler_version,
        spec_slice=first.nodes[0]._spec_slice,  # noqa: SLF001
        upstream_data_versions={"mvar": "data"},
    )
    second_key = compute_idempotency_key(
        rule_id=1,
        node_id=second.nodes[0].node_id,
        check_id=second.nodes[0].check_id,
        scope_hash="scope",
        config_version="config",
        code_version="code",
        runtime_identity={
            "ba": "ECR",
            "business_date": "2026-06-04",
            "run_type": "1dvar",
            "snapshot_id": "fixture",
        },
        handler_version=HANDLERS["mvar"].handler_version,
        spec_slice=second.nodes[0]._spec_slice,  # noqa: SLF001
        upstream_data_versions={"mvar": "data"},
    )

    assert first_key != second_key
