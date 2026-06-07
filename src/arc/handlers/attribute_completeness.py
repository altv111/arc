from __future__ import annotations

from typing import Any, ClassVar

from arc.core.results import HandlerOutput, InputSpec, NodeStatus, ResolvedInputs
from arc.handlers._common import UPSTREAM_RESULTS_KEY, upstream_results
from arc.handlers.registry import CheckHandler, register


@register
class AttributeCompletenessDrilldownHandler(CheckHandler):
    check_id: ClassVar[str] = "attribute_completeness_drilldown"
    handler_version: ClassVar[str] = "1.0.0"

    input_spec: ClassVar[InputSpec] = InputSpec(datasets={UPSTREAM_RESULTS_KEY: {}})

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        failed = [r for r in upstream_results(inputs) if r.status is NodeStatus.FAIL]
        attributed = [
            {
                "node_id": result.node_id,
                "check_id": result.check_id,
                "node_type": result.node_type,
                "breached_scope_levels": [
                    scope.model_dump(mode="json")["levels"] for scope in result.breached_scopes
                ],
                "metrics": result.metrics,
            }
            for result in failed
        ]

        return HandlerOutput(
            status=NodeStatus.PASS,
            metrics={"n_failed_upstream_nodes": len(failed)},
            evidence_payloads={"attribution": {"items": attributed}},
            downstream_hints={"attributed_breaches": attributed},
        )
