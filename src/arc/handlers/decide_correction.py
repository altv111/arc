from __future__ import annotations

from typing import Any, ClassVar

from arc.core.results import HandlerOutput, InputSpec, NodeStatus, ResolvedInputs
from arc.handlers._common import UPSTREAM_RESULTS_KEY, upstream_results
from arc.handlers.registry import CheckHandler, register


@register
class DecideCorrectionHandler(CheckHandler):
    check_id: ClassVar[str] = "decide_correction"
    handler_version: ClassVar[str] = "1.0.0"

    input_spec: ClassVar[InputSpec] = InputSpec(datasets={UPSTREAM_RESULTS_KEY: {}})

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        attributed = []
        for result in upstream_results(inputs):
            if result.node_type == "attribute":
                attributed.extend(result.downstream_hints.get("attributed_breaches") or [])

        decisions = [
            {
                "decision": _decision_for(item),
                "source_node_id": item["node_id"],
                "check_id": item["check_id"],
                "breached_scope_levels": item["breached_scope_levels"],
                "policy": "missing_trade_v1",
                "classification": item.get("classification"),
                "late_arrival_possible": item.get("late_arrival_possible"),
            }
            for item in attributed
        ]

        return HandlerOutput(
            status=NodeStatus.PASS,
            metrics={"n_decisions": len(decisions)},
            evidence_payloads={
                "decisions": {
                    "policy": "missing_trade_v1",
                    "items": decisions,
                }
            },
            downstream_hints={"decisions": decisions},
        )


def _decision_for(attributed_breach: dict[str, Any]) -> str:
    recommended = attributed_breach.get("recommended_decision")
    if recommended in {"roll", "hold", "fill_from_live"}:
        return recommended

    classification = attributed_breach.get("classification")
    if classification == "expected_to_arrive_late":
        return "hold"
    return "roll"
