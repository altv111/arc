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
        options = tuple(spec_slice.get("decision_options") or ())
        decision = "fill_from_yesterday" if "fill_from_yesterday" in options else (options[0] if options else "hold_recheck")

        attributed = []
        for result in upstream_results(inputs):
            if result.check_id == "attribute_completeness_drilldown":
                attributed = list(result.downstream_hints.get("attributed_breaches") or [])
                break

        decisions = [
            {
                "decision": decision,
                "source_node_id": item["node_id"],
                "check_id": item["check_id"],
                "breached_scope_levels": item["breached_scope_levels"],
            }
            for item in attributed
        ]

        return HandlerOutput(
            status=NodeStatus.PASS,
            metrics={"n_decisions": len(decisions)},
            evidence_payloads={"decisions": {"items": decisions}},
            downstream_hints={"decisions": decisions},
        )
