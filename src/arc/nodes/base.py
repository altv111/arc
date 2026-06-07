from __future__ import annotations

import concurrent.futures
import hashlib
import json
from abc import ABC
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, ClassVar, Protocol, runtime_checkable

from arc.core.blackboard import Blackboard
from arc.core.context import BARunContext, ScopeKey
from arc.core.evidence_store import EvidenceStore
from arc.core.results import (
    EvidenceRef,
    HandlerOutput,
    IndeterminateError,
    NodeResult,
    NodeStatus,
    ResolvedInputs,
)
from arc.core.run_state import RunStateStore

DEFAULT_TIMEOUT_SECONDS = 60.0


@runtime_checkable
class HandlerProtocol(Protocol):
    check_id: str
    handler_version: str
    check_grain: str | None

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        ...


def compute_idempotency_key(
    *,
    rule_id: int,
    node_id: str,
    check_id: str,
    scope_hash: str,
    config_version: str,
    code_version: str,
    handler_version: str,
    spec_slice: dict[str, Any],
    upstream_data_versions: dict[str, str],
) -> str:
    payload = {
        "rule_id": rule_id,
        "node_id": node_id,
        "check_id": check_id,
        "scope_hash": scope_hash,
        "config_version": config_version,
        "code_version": code_version,
        "handler_version": handler_version,
        "spec_slice": spec_slice,
        "upstream_data_versions": dict(sorted(upstream_data_versions.items())),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Node(ABC):
    node_type: ClassVar[str] = ""

    def __init__(
        self,
        *,
        rule_id: int,
        node_id: str | None = None,
        check_id: str,
        spec_slice: dict[str, Any],
        handler: HandlerProtocol,
        evidence_store: EvidenceStore,
        run_state_store: RunStateStore,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not self.node_type:
            raise TypeError(f"{type(self).__name__} must declare non-empty node_type")
        if not check_id:
            raise ValueError("check_id must not be empty")
        if node_id is not None and not node_id:
            raise ValueError("node_id must not be empty")
        if handler.check_id != check_id:
            raise ValueError(f"handler.check_id={handler.check_id!r} != node.check_id={check_id!r}")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._rule_id = rule_id
        self._node_id = node_id or check_id
        self._check_id = check_id
        self._spec_slice = spec_slice
        self._handler = handler
        self._evidence_store = evidence_store
        self._run_state_store = run_state_store
        self._timeout_seconds = timeout_seconds

    @property
    def rule_id(self) -> int:
        return self._rule_id

    @property
    def check_id(self) -> str:
        return self._check_id

    @property
    def node_id(self) -> str:
        return self._node_id

    def run(
        self,
        ctx: BARunContext,
        blackboard: Blackboard,
        resolved_inputs: ResolvedInputs,
        scope: ScopeKey,
    ) -> NodeResult:
        idempotency_key = compute_idempotency_key(
            rule_id=self._rule_id,
            node_id=self._node_id,
            check_id=self._check_id,
            scope_hash=scope.canonical_hash(),
            config_version=ctx.config_version,
            code_version=ctx.code_version,
            handler_version=self._handler.handler_version,
            spec_slice=self._spec_slice,
            upstream_data_versions=resolved_inputs.versions,
        )

        cached = self._run_state_store.get(idempotency_key)
        if cached is not None:
            blackboard.put(cached, scope, overwrite=True)
            return cached

        started = datetime.now(tz=timezone.utc)
        status: NodeStatus
        metrics: dict[str, float | int | None] = {}
        breached: list[ScopeKey] = []
        evidence_refs: list[EvidenceRef] = []
        downstream_hints: dict[str, Any] = {}

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                spec_slice = self._augmented_spec_slice(ctx)
                future = executor.submit(
                    self._handler.execute,
                    resolved_inputs,
                    spec_slice,
                )
                output = future.result(timeout=self._timeout_seconds)
                output = self._post_process_output(output, ctx, resolved_inputs, spec_slice)
        except concurrent.futures.TimeoutError:
            status = NodeStatus.INDETERMINATE
            downstream_hints["timeout_seconds"] = self._timeout_seconds
            downstream_hints["error_kind"] = "timeout"
        except IndeterminateError as exc:
            status = NodeStatus.INDETERMINATE
            downstream_hints["error_kind"] = "indeterminate"
            downstream_hints["reason"] = exc.reason
            if exc.details:
                downstream_hints["details"] = exc.details
        except Exception as exc:  # noqa: BLE001 - intentional boundary catch
            status = NodeStatus.ERROR
            downstream_hints["error_kind"] = type(exc).__name__
            downstream_hints["reason"] = str(exc)
        else:
            for name, payload in output.evidence_payloads.items():
                evidence_refs.append(self._evidence_store.put(name, payload))
            status = output.status
            metrics = dict(output.metrics)
            breached = list(output.breached_scopes)
            downstream_hints = dict(output.downstream_hints)

        finished = datetime.now(tz=timezone.utc)
        duration_ms = max(int((finished - started).total_seconds() * 1000), 0)

        result = NodeResult(
            rule_id=self._rule_id,
            node_id=self._node_id,
            check_id=self._check_id,
            node_type=self.node_type,
            status=status,
            metrics=metrics,
            breached_scopes=breached,
            evidence_refs=evidence_refs,
            downstream_hints=downstream_hints,
            handler_version=self._handler.handler_version,
            config_version=ctx.config_version,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            upstream_data_versions=dict(resolved_inputs.versions),
        )

        self._run_state_store.put(idempotency_key, result)
        blackboard.put(result, scope, overwrite=True)
        return result

    def _augmented_spec_slice(self, ctx: BARunContext) -> dict[str, Any]:
        reserved = {
            "__ba",
            "__business_date",
            "__run_type",
            "__snapshot_id",
            "__config_version",
            "__code_version",
        }
        overlap = reserved.intersection(self._spec_slice)
        if overlap:
            raise ValueError(f"spec_slice may not define reserved runtime keys: {sorted(overlap)}")
        merged = dict(self._spec_slice)
        merged["__ba"] = ctx.ba
        merged["__business_date"] = ctx.business_date.isoformat()
        merged["__run_type"] = ctx.run_type
        merged["__snapshot_id"] = ctx.snapshot_id
        merged["__config_version"] = ctx.config_version
        merged["__code_version"] = ctx.code_version
        return merged

    def _post_process_output(
        self,
        output: HandlerOutput,
        ctx: BARunContext,
        resolved_inputs: ResolvedInputs,
        spec_slice: dict[str, Any],
    ) -> HandlerOutput:
        return output
