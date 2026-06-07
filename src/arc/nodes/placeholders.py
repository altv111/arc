from __future__ import annotations

from typing import Any, ClassVar

from arc.clients.correction import CorrectionClient, ShadowCorrectionClient
from arc.core.context import BARunContext
from arc.core.results import HandlerOutput, NodeStatus, ResolvedInputs
from arc.handlers.registry import CheckHandler
from arc.nodes.base import Node


class NoopHandler(CheckHandler):
    check_id: ClassVar[str] = "noop"
    handler_version: ClassVar[str] = "1.0.0"

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        return HandlerOutput(status=NodeStatus.PASS)


class AttributeNode(Node):
    node_type: ClassVar[str] = "attribute"


class DecideNode(Node):
    node_type: ClassVar[str] = "decide"


class ActNode(Node):
    node_type: ClassVar[str] = "act"

    def __init__(
        self,
        *,
        correction_client: CorrectionClient | None = None,
        **kwargs: Any,
    ) -> None:
        correction_client = correction_client or ShadowCorrectionClient()
        spec_slice = dict(kwargs["spec_slice"])
        spec_slice["correction_client_id"] = correction_client.client_id
        spec_slice["correction_client_version"] = correction_client.client_version
        kwargs["spec_slice"] = spec_slice
        super().__init__(**kwargs)
        self._correction_client = correction_client

    def _post_process_output(
        self,
        output: HandlerOutput,
        ctx: BARunContext,
        resolved_inputs: ResolvedInputs,
        spec_slice: dict[str, Any],
    ) -> HandlerOutput:
        intents = tuple(output.downstream_hints.get("correction_intents") or ())
        mode = str(spec_slice.get("mode") or "shadow")
        receipt = self._correction_client.submit_intents(intents, mode=mode)
        evidence_payloads = {
            **output.evidence_payloads,
            "correction_receipt": receipt,
        }
        downstream_hints = {
            **output.downstream_hints,
            "correction_receipt": receipt,
        }
        metrics = {
            **output.metrics,
            "n_correction_receipts": 1,
        }
        return output.model_copy(
            update={
                "metrics": metrics,
                "evidence_payloads": evidence_payloads,
                "downstream_hints": downstream_hints,
            }
        )


class RecordNode(Node):
    node_type: ClassVar[str] = "record"
