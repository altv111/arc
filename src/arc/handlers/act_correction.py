from __future__ import annotations

from typing import Any, ClassVar

from arc.core.results import HandlerOutput, InputSpec, NodeStatus, ResolvedInputs
from arc.handlers._common import UPSTREAM_RESULTS_KEY, upstream_results
from arc.handlers.registry import CheckHandler, register


@register
class ActCorrectionHandler(CheckHandler):
    check_id: ClassVar[str] = "act_correction"
    handler_version: ClassVar[str] = "1.0.0"

    input_spec: ClassVar[InputSpec] = InputSpec(datasets={UPSTREAM_RESULTS_KEY: {}})

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        mode = spec_slice.get("mode") or "shadow"
        decisions = []
        for result in upstream_results(inputs):
            if result.check_id == "decide_correction":
                decisions = list(result.downstream_hints.get("decisions") or [])
                break

        intents = [
            {
                "mode": mode,
                "intent": decision["decision"],
                "source_node_id": decision["source_node_id"],
                "check_id": decision["check_id"],
                "breached_scope_levels": decision["breached_scope_levels"],
            }
            for decision in decisions
        ]

        return HandlerOutput(
            status=NodeStatus.PASS,
            metrics={"n_correction_intents": len(intents)},
            evidence_payloads={"correction_intents": {"items": intents}},
            downstream_hints={"correction_intents": intents},
        )
