from __future__ import annotations

from typing import Any, ClassVar

from arc.core.results import HandlerOutput, InputSpec, NodeStatus, ResolvedInputs
from arc.handlers._common import UPSTREAM_RESULTS_KEY, upstream_results
from arc.handlers.registry import CheckHandler, register


@register
class RecordBreachHandler(CheckHandler):
    check_id: ClassVar[str] = "record_breach"
    handler_version: ClassVar[str] = "1.0.0"

    input_spec: ClassVar[InputSpec] = InputSpec(datasets={UPSTREAM_RESULTS_KEY: {}})

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        rows = upstream_results(inputs)
        correction_intents = []
        for result in rows:
            if result.check_id == "act_correction":
                correction_intents = list(result.downstream_hints.get("correction_intents") or [])
                break

        record = {
            "rule_id": rows[0].rule_id if rows else None,
            "parent_scope": spec_slice.get("parent_scope") or {},
            "node_summaries": [
                {
                    "node_id": result.node_id,
                    "node_type": result.node_type,
                    "check_id": result.check_id,
                    "status": result.status.value,
                    "metrics": result.metrics,
                    "evidence_refs": [
                        evidence.model_dump(mode="json") for evidence in result.evidence_refs
                    ],
                }
                for result in rows
            ],
            "correction_intents": correction_intents,
        }

        return HandlerOutput(
            status=NodeStatus.PASS,
            metrics={
                "n_recorded_nodes": len(rows),
                "n_correction_intents": len(correction_intents),
            },
            evidence_payloads={"breach_record": record},
            downstream_hints={"breach_record": record},
        )
