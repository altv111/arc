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
from arc.runner import Runner
from arc.visualize import render_rule_mermaid, render_rule_plan, render_rule_rich

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
        "evaluate:mvar:0",
        "evaluate:mvar:1",
        "attribute",
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
    assert "n_evaluate_mvar_0 --> n_attribute" in rendered
    assert "n_attribute --> n_decide" in rendered
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
    assert "datasets   : completeness_summary" in rendered
    assert "datasets   : dod_var_extract" in rendered
    assert "datasets   : mvar" in rendered
    assert "where      : parent_scope + ubr_level_9=Europe Linear Flow" in rendered
    assert "emits      : correction decisions" in rendered
    assert "emits      : correction intents + correction receipt" in rendered


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
    assert "dod_var_extract" in rendered
    assert "Compute and decision paths" in rendered


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
    assert gate.metrics["missing_count"] == 4
    assert gate.metrics["total_count"] == 150
    assert len(gate.breached_scopes) == 2
    assert all(
        scope.levels["ubr_level_8"] == ("Europe Core Rates",)
        for scope in gate.breached_scopes
    )

    dod = next(result for result in report.results if result.node_id == "evaluate:dod_var_move:0")
    assert dod.metrics["n_scopes_examined"] == 2
    assert dod.metrics["n_breached_scopes"] == 2
    assert all(scope.levels["ubr_level_8"] == ("Europe Core Rates",) for scope in dod.breached_scopes)

    mvar_linear = next(result for result in report.results if result.node_id == "evaluate:mvar:0")
    mvar_nonlinear = next(result for result in report.results if result.node_id == "evaluate:mvar:1")
    assert mvar_linear.metrics["n_scopes_examined"] == 1
    assert mvar_nonlinear.metrics["n_scopes_examined"] == 1
    assert mvar_linear.breached_scopes[0].levels["ubr_level_9"] == ("Europe Linear Flow",)
    assert mvar_nonlinear.breached_scopes[0].levels["ubr_level_9"] == ("Europe Non Linear",)

    record = next(result for result in report.results if result.node_id == "record")
    assert record.metrics["n_correction_intents"] == 4
    act = next(result for result in report.results if result.node_id == "act")
    assert act.metrics["n_correction_receipts"] == 1
    assert act.downstream_hints["correction_receipt"]["client"] == "ShadowCorrectionClient"
    assert act.downstream_hints["correction_receipt"]["mutation_performed"] is False
    assert act.downstream_hints["correction_receipt"]["n_intents"] == 4
    assert (tmp_path / "runs" / "row1-test" / "run.json").exists()


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
        handler_version=HANDLERS["mvar"].handler_version,
        spec_slice=second.nodes[0]._spec_slice,  # noqa: SLF001
        upstream_data_versions={"mvar": "data"},
    )

    assert first_key != second_key
