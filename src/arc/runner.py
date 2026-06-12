from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arc.clients.reporting import ReportingClient
from arc.core.blackboard import Blackboard
from arc.core.context import BARunContext, ScopeKey
from arc.core.evidence_store import EvidenceStore
from arc.core.results import InputSpec, NodeResult, NodeStatus, ResolvedInputs
from arc.core.run_state import RunStateStore
from arc.handlers._common import UPSTREAM_RESULTS_KEY, make_upstream_bundle
from arc.nodes.base import Node
from arc.rule import Rule


class DatasetResolver:
    def __init__(self, client: ReportingClient) -> None:
        self._client = client
        self._dispatch = {
            "completeness_summary": client.get_completeness_summary,
            "dod_var_extract": client.get_dod_var_extract,
            "mvar": client.get_mvar,
            "tminus1_trade_mvar": client.get_tminus1_trade_mvar,
            "rf_sensi": client.get_rf_sensi,
            "kannon_sensi": client.get_kannon_sensi,
            "trade_completeness": client.get_trade_completeness,
            "completeness_exception_report": client.get_completeness_exception_report,
            "kannon_trade_level_sensi": client.get_kannon_trade_level_sensi,
            "riskfinder_calc_status": client.get_riskfinder_calc_status,
            "historical_trade_status": client.get_historical_trade_status,
            "upstream_trade_presence": client.get_upstream_trade_presence,
        }
        self._cache: dict[tuple[str, str, str, str], Any] = {}

    def resolve(
        self,
        input_spec: InputSpec,
        ctx: BARunContext,
        parent_scope: dict[str, list[str]],
    ) -> ResolvedInputs:
        data: dict[str, Any] = {}
        versions: dict[str, str] = {}

        for name in input_spec.datasets:
            if name == UPSTREAM_RESULTS_KEY:
                continue
            try:
                fn = self._dispatch[name]
            except KeyError as exc:
                raise KeyError(f"no resolver for dataset {name!r}; known: {sorted(self._dispatch)}") from exc
            params = input_spec.datasets[name]
            cache_key = (
                name,
                ctx.ba,
                ctx.business_date.isoformat(),
                ctx.snapshot_id,
                json.dumps(parent_scope, sort_keys=True, separators=(",", ":")),
                json.dumps(params, sort_keys=True, separators=(",", ":")),
            )
            ds = self._cache.get(cache_key)
            if ds is None:
                ds = _filter_dataset(fn(ctx.ba, ctx.business_date, **params), parent_scope)
                ds = _filter_dataset_by_params(ds, params)
                self._cache[cache_key] = ds
            data[name] = ds
            versions[name] = ds.content_hash

        return ResolvedInputs(data=data, versions=versions)


@dataclass(frozen=True)
class SkippedNode:
    node_index: int
    node_id: str
    node_type: str
    check_id: str
    reason: str


@dataclass
class RunReport:
    run_id: str
    ctx: BARunContext
    rule_id: int
    started_at: datetime
    finished_at: datetime
    results: list[NodeResult] = field(default_factory=list)
    skipped: list[SkippedNode] = field(default_factory=list)
    artifacts_dir: Path | None = None

    @property
    def status(self) -> NodeStatus:
        order = {
            NodeStatus.ERROR: 3,
            NodeStatus.INDETERMINATE: 2,
            NodeStatus.FAIL: 1,
            NodeStatus.PASS: 0,
        }
        if not self.results:
            return NodeStatus.PASS
        return max((r.status for r in self.results), key=lambda s: order[s])

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "rule_id": self.rule_id,
            "ba": self.ctx.ba,
            "business_date": self.ctx.business_date.isoformat(),
            "snapshot_id": self.ctx.snapshot_id,
            "config_version": self.ctx.config_version,
            "code_version": self.ctx.code_version,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": int((self.finished_at - self.started_at).total_seconds() * 1000),
            "status": self.status.value,
            "n_executed": len(self.results),
            "n_skipped": len(self.skipped),
            "results": [
                {
                    "node_id": r.node_id,
                    "node_type": r.node_type,
                    "check_id": r.check_id,
                    "status": r.status.value,
                    "duration_ms": r.duration_ms,
                    "n_breached_scopes": len(r.breached_scopes),
                    "n_evidence_refs": len(r.evidence_refs),
                }
                for r in self.results
            ],
            "skipped": [
                {
                    "node_index": s.node_index,
                    "node_id": s.node_id,
                    "node_type": s.node_type,
                    "check_id": s.check_id,
                    "reason": s.reason,
                }
                for s in self.skipped
            ],
        }


class Runner:
    SUPPORTED_GATE_LOGIC = frozenset(
        {
            "run_evaluates_only_if_any_gate_breaches",
            "run_evaluates_always",
        }
    )

    def __init__(
        self,
        *,
        reporting_client: ReportingClient,
        evidence_store: EvidenceStore,
        run_state_store: RunStateStore,
        runs_root: Path,
    ) -> None:
        self._resolver = DatasetResolver(reporting_client)
        self._evidence_store = evidence_store
        self._run_state_store = run_state_store
        self._runs_root = Path(runs_root)
        self._runs_root.mkdir(parents=True, exist_ok=True)

    def run_rule(
        self,
        rule: Rule,
        ctx: BARunContext,
        scope: ScopeKey | None = None,
        *,
        blackboard: Blackboard | None = None,
        run_id: str | None = None,
    ) -> RunReport:
        if rule.gate_logic not in self.SUPPORTED_GATE_LOGIC:
            raise ValueError(
                f"unsupported gate_logic={rule.gate_logic!r}; "
                f"supported: {sorted(self.SUPPORTED_GATE_LOGIC)}"
            )

        scope = scope or ScopeKey()
        blackboard = blackboard or Blackboard()
        run_id = run_id or uuid.uuid4().hex
        started = datetime.now(tz=timezone.utc)

        results: list[NodeResult] = []
        skipped: list[SkippedNode] = []

        gates = rule.nodes_of_type("gate")
        evaluates = rule.nodes_of_type("evaluate")
        correction_chain = [
            n for n in rule.nodes if n.node_type not in {"gate", "evaluate"}
        ]

        for node in gates:
            results.append(self._execute(node, ctx, blackboard, scope, results, rule.parent_scope))

        gate_results = [r for r in results if r.node_type == "gate"]
        if not self._should_run_evaluates(rule.gate_logic, gate_results):
            skipped.extend(self._skip_nodes(rule, evaluates + correction_chain, "gate_logic_skipped: no gate breached"))
            return self._finish_report(run_id, ctx, rule, started, results, skipped)

        for node in evaluates:
            results.append(self._execute(node, ctx, blackboard, scope, results, rule.parent_scope))

        evaluate_results = [r for r in results if r.node_type == "evaluate"]
        if not any(r.status is NodeStatus.FAIL for r in evaluate_results):
            skipped.extend(self._skip_nodes(rule, correction_chain, "correction_chain_skipped: no evaluate breached"))
            return self._finish_report(run_id, ctx, rule, started, results, skipped)

        for node in correction_chain:
            results.append(self._execute(node, ctx, blackboard, scope, results, rule.parent_scope))

        return self._finish_report(run_id, ctx, rule, started, results, skipped)

    def _execute(
        self,
        node: Node,
        ctx: BARunContext,
        blackboard: Blackboard,
        scope: ScopeKey,
        prior_results: list[NodeResult],
        parent_scope: dict[str, list[str]],
    ) -> NodeResult:
        input_spec = node._handler.plan_inputs(node.planning_spec(ctx), tuple(prior_results))  # noqa: SLF001
        resolved = self._resolver.resolve(input_spec, ctx, parent_scope)

        if UPSTREAM_RESULTS_KEY in input_spec.datasets:
            resolved = self._inject_upstream(resolved, prior_results)

        return node.run(ctx, blackboard, resolved, scope)

    @staticmethod
    def _inject_upstream(resolved: ResolvedInputs, prior_results: list[NodeResult]) -> ResolvedInputs:
        bundle = make_upstream_bundle(prior_results)
        data = {**resolved.data, UPSTREAM_RESULTS_KEY: bundle}
        versions = {**resolved.versions, UPSTREAM_RESULTS_KEY: bundle.content_hash}
        return ResolvedInputs(data=data, versions=versions)

    @staticmethod
    def _should_run_evaluates(gate_logic: str, gate_results: list[NodeResult]) -> bool:
        if gate_logic == "run_evaluates_always":
            return True
        return any(r.status is NodeStatus.FAIL for r in gate_results)

    @staticmethod
    def _skip_nodes(rule: Rule, nodes: list[Node], reason: str) -> list[SkippedNode]:
        return [
            SkippedNode(
                node_index=rule.nodes.index(node),
                node_id=node.node_id,
                node_type=node.node_type,
                check_id=node.check_id,
                reason=reason,
            )
            for node in nodes
        ]

    def _finish_report(
        self,
        run_id: str,
        ctx: BARunContext,
        rule: Rule,
        started: datetime,
        results: list[NodeResult],
        skipped: list[SkippedNode],
    ) -> RunReport:
        finished = datetime.now(tz=timezone.utc)
        report = RunReport(
            run_id=run_id,
            ctx=ctx,
            rule_id=rule.row_id,
            started_at=started,
            finished_at=finished,
            results=results,
            skipped=skipped,
        )
        self._persist(report)
        return report

    def _persist(self, report: RunReport) -> None:
        run_dir = self._runs_root / report.run_id
        results_dir = run_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        for i, result in enumerate(report.results):
            name = f"{i:02d}_{result.node_type}_{_safe(result.node_id)}.json"
            (results_dir / name).write_text(result.model_dump_json(indent=2), encoding="utf-8")

        (run_dir / "run.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        report.artifacts_dir = run_dir


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)


def _filter_dataset(dataset: Any, scope: dict[str, list[str]]) -> Any:
    if not scope:
        return dataset

    filtered_rows = []
    for row in dataset.rows:
        if _row_matches_scope(row, scope):
            filtered_rows.append(row)

    payload = [
        row.model_dump(mode="json") if hasattr(row, "model_dump") else row
        for row in filtered_rows
    ]
    content_hash = json_hash({"source": dataset.content_hash, "scope": scope, "rows": payload})
    return type(dataset)(rows=tuple(filtered_rows), content_hash=content_hash)


def _filter_dataset_by_params(dataset: Any, params: dict[str, Any]) -> Any:
    filters = {
        "portfolio_names": "portfolio",
        "trade_ids": "trade_id",
        "risk_type_list": "risk_type",
        "sensitivity_type_list": "sensitivity_type",
        "var_type": "var_type",
    }
    effective = {
        attr: _as_allowed(params[key])
        for key, attr in filters.items()
        if key in params and _as_allowed(params[key])
    }
    if not effective:
        return dataset

    filtered_rows = []
    for row in dataset.rows:
        keep = True
        for attr, allowed in effective.items():
            if not hasattr(row, attr):
                continue
            if getattr(row, attr) not in allowed:
                keep = False
                break
        if keep:
            filtered_rows.append(row)

    payload = [
        row.model_dump(mode="json") if hasattr(row, "model_dump") else row
        for row in filtered_rows
    ]
    content_hash = json_hash({"source": dataset.content_hash, "params": params, "rows": payload})
    return type(dataset)(rows=tuple(filtered_rows), content_hash=content_hash)


def _as_allowed(value: Any) -> set[Any]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return set(value)
    return {value}


def _row_matches_scope(row: Any, scope: dict[str, list[str]]) -> bool:
    for key, allowed in scope.items():
        if not hasattr(row, key):
            return False
        value = getattr(row, key)
        if value is None:
            return False
        if value not in set(allowed):
            return False
    return True


def json_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
