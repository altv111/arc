from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arc.clients.correction import CorrectionClient
from arc.core.evidence_store import EvidenceStore
from arc.core.run_state import RunStateStore
from arc.handlers.registry import HANDLERS
from arc.nodes.base import Node
from arc.nodes.concrete import EvaluateNode, GateNode
from arc.nodes.placeholders import ActNode, AttributeNode, DecideNode, NoopHandler, RecordNode


class ImpactCheckSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    check_id: str = Field(min_length=1)
    mode: Literal["gate", "evaluate"]
    check_grain: str = Field(min_length=1)
    on_breach: str | None = None
    threshold_default: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)


class EvaluationPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    gate_logic: Literal[
        "run_evaluates_only_if_any_gate_breaches",
        "run_evaluates_always",
    ] = "run_evaluates_only_if_any_gate_breaches"
    evaluate_logic: Literal["all_applicable_evaluates"] = "all_applicable_evaluates"


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    row_id: int = Field(ge=1)
    status: str = "enabled"
    parent_scope: dict[str, list[str]] = Field(default_factory=dict)
    impact_checks: list[ImpactCheckSpec] = Field(min_length=1)
    evaluation_policy: EvaluationPolicy = Field(default_factory=EvaluationPolicy)
    decision_options: list[str] = Field(default_factory=list)
    correction_mapping_ref: str | None = None

    attribute_handler: str | None = None
    decide_handler: str | None = None
    act_handler: str | None = None
    act_mode: str = "shadow"
    record_handler: str | None = None

    @model_validator(mode="after")
    def _at_least_one_gate(self) -> "RuleSpec":
        modes = {ic.mode for ic in self.impact_checks}
        if "gate" not in modes:
            raise ValueError("rule must declare at least one impact_check with mode='gate'")
        return self


class Rule(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    row_id: int
    spec: RuleSpec
    nodes: list[Node]

    @property
    def gate_logic(self) -> str:
        return self.spec.evaluation_policy.gate_logic

    @property
    def evaluate_logic(self) -> str:
        return self.spec.evaluation_policy.evaluate_logic

    @property
    def decision_options(self) -> tuple[str, ...]:
        return tuple(self.spec.decision_options)

    def nodes_of_type(self, node_type: str) -> list[Node]:
        return [n for n in self.nodes if n.node_type == node_type]

    @property
    def parent_scope(self) -> dict[str, list[str]]:
        return self.spec.parent_scope


def build_rule(
    spec: RuleSpec | dict[str, Any],
    *,
    evidence_store: EvidenceStore,
    run_state_store: RunStateStore,
    correction_client: CorrectionClient | None = None,
) -> Rule:
    rule_spec = spec if isinstance(spec, RuleSpec) else RuleSpec(**spec)
    nodes: list[Node] = []

    gates = [ic for ic in rule_spec.impact_checks if ic.mode == "gate"]
    evals = [ic for ic in rule_spec.impact_checks if ic.mode == "evaluate"]

    for ic in gates:
        nodes.extend(_check_nodes(GateNode, rule_spec.row_id, ic, evidence_store, run_state_store))

    for ic in evals:
        nodes.extend(_check_nodes(EvaluateNode, rule_spec.row_id, ic, evidence_store, run_state_store))

    attribute = _handler_or_noop(rule_spec.attribute_handler)
    nodes.append(
        AttributeNode(
            rule_id=rule_spec.row_id,
            node_id="attribute",
            check_id=attribute.check_id,
            spec_slice={"parent_scope": rule_spec.parent_scope},
            handler=attribute,
            evidence_store=evidence_store,
            run_state_store=run_state_store,
        )
    )
    decide = _handler_or_noop(rule_spec.decide_handler)
    nodes.append(
        DecideNode(
            rule_id=rule_spec.row_id,
            node_id="decide",
            check_id=decide.check_id,
            spec_slice={"decision_options": list(rule_spec.decision_options)},
            handler=decide,
            evidence_store=evidence_store,
            run_state_store=run_state_store,
        )
    )
    act = _handler_or_noop(rule_spec.act_handler)
    nodes.append(
        ActNode(
            rule_id=rule_spec.row_id,
            node_id="act",
            check_id=act.check_id,
            spec_slice={"mode": rule_spec.act_mode},
            handler=act,
            evidence_store=evidence_store,
            run_state_store=run_state_store,
            correction_client=correction_client,
        )
    )
    record = _handler_or_noop(rule_spec.record_handler)
    nodes.append(
        RecordNode(
            rule_id=rule_spec.row_id,
            node_id="record",
            check_id=record.check_id,
            spec_slice={"parent_scope": rule_spec.parent_scope},
            handler=record,
            evidence_store=evidence_store,
            run_state_store=run_state_store,
        )
    )

    return Rule(row_id=rule_spec.row_id, spec=rule_spec, nodes=nodes)


def build_rule_from_json(
    path: Path,
    *,
    evidence_store: EvidenceStore,
    run_state_store: RunStateStore,
    correction_client: CorrectionClient | None = None,
) -> Rule:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_rule(
        payload,
        evidence_store=evidence_store,
        run_state_store=run_state_store,
        correction_client=correction_client,
    )


def _check_nodes(
    cls: type[Node],
    row_id: int,
    ic: ImpactCheckSpec,
    evidence_store: EvidenceStore,
    run_state_store: RunStateStore,
) -> list[Node]:
    try:
        handler = HANDLERS[ic.check_id]
    except KeyError as exc:
        raise KeyError(f"row {row_id}: no handler registered for check_id={ic.check_id!r}") from exc

    supported_grains = getattr(handler, "supported_check_grains", None)
    handler_grain = getattr(handler, "check_grain", None)
    if supported_grains and ic.check_grain not in supported_grains:
        raise ValueError(
            f"row {row_id}: check_id={ic.check_id!r} declares check_grain={ic.check_grain!r} "
            f"but handler supports {sorted(supported_grains)!r}"
        )
    if not supported_grains and handler_grain and handler_grain != ic.check_grain:
        raise ValueError(
            f"row {row_id}: check_id={ic.check_id!r} declares check_grain={ic.check_grain!r} "
            f"but handler requires {handler_grain!r}"
        )

    rows = ic.rows or [{"check_scope": {}, "breach_level": ic.check_grain, "threshold": ic.threshold_default or {}}]
    nodes: list[Node] = []
    check_payload = ic.model_dump(exclude={"rows"}, mode="json")
    for index, row in enumerate(rows):
        spec_slice = {
            **check_payload,
            "row_index": index,
            "row_id": f"{ic.check_id}:{index}",
            **row,
        }
        nodes.append(
            cls(
                rule_id=row_id,
                node_id=f"{ic.mode}:{ic.check_id}:{index}",
                check_id=ic.check_id,
                spec_slice=spec_slice,
                handler=handler,
                evidence_store=evidence_store,
                run_state_store=run_state_store,
            )
        )
    return nodes


def _handler_or_noop(check_id: str | None):
    if not check_id:
        return NoopHandler()
    try:
        return HANDLERS[check_id]
    except KeyError as exc:
        raise KeyError(f"no handler registered for check_id={check_id!r}") from exc
