from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from arc.core.results import IndeterminateError, NodeResult, ResolvedInputs

UPSTREAM_RESULTS_KEY = "_upstream_results"


@dataclass(frozen=True)
class UpstreamBundle:
    rows: tuple[NodeResult, ...]
    content_hash: str
    content_length: int = 0


def _json_stable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_stable(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_json_stable(v) for v in obj]
    return obj


def make_upstream_bundle(results: list[NodeResult]) -> UpstreamBundle:
    canon: list[dict[str, Any]] = []
    for result in results:
        canon.append(
            {
                "rule_id": result.rule_id,
                "check_id": result.check_id,
                "node_type": result.node_type,
                "status": result.status.value,
                "metrics": dict(sorted(result.metrics.items())),
                "breached_scope_hashes": sorted(s.canonical_hash() for s in result.breached_scopes),
                "evidence_content_hashes": sorted(
                    json.dumps(
                        {"name": e.name, "content_hash": e.content_hash},
                        sort_keys=True,
                    )
                    for e in result.evidence_refs
                ),
                "downstream_hints": _json_stable(result.downstream_hints),
                "handler_version": result.handler_version,
                "config_version": result.config_version,
                "upstream_data_versions": dict(sorted(result.upstream_data_versions.items())),
            }
        )
    payload = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return UpstreamBundle(
        rows=tuple(results),
        content_hash=hashlib.sha256(payload).hexdigest(),
        content_length=len(payload),
    )


def upstream_results(inputs: ResolvedInputs) -> tuple[NodeResult, ...]:
    if UPSTREAM_RESULTS_KEY not in inputs.data:
        raise IndeterminateError(
            f"required input {UPSTREAM_RESULTS_KEY!r} missing from ResolvedInputs",
            details={"input": UPSTREAM_RESULTS_KEY},
        )
    bundle = inputs.data[UPSTREAM_RESULTS_KEY]
    rows = tuple(getattr(bundle, "rows", bundle))
    for row in rows:
        if not isinstance(row, NodeResult):
            raise TypeError(f"{UPSTREAM_RESULTS_KEY!r} contains non-NodeResult: {type(row).__name__}")
    return rows


def missing_trades_from_results(results: tuple[NodeResult, ...]) -> tuple[dict[str, Any], ...]:
    missing: dict[str, dict[str, Any]] = {}
    for result in results:
        for row in result.downstream_hints.get("missing_trades") or []:
            trade_id = row.get("trade_id")
            hierarchy = row.get("hierarchy")
            if trade_id and isinstance(hierarchy, dict):
                missing[trade_id] = row
    return tuple(missing[key] for key in sorted(missing))


def missing_trade_ids_from_results(results: tuple[NodeResult, ...]) -> tuple[str, ...]:
    return tuple(row["trade_id"] for row in missing_trades_from_results(results))


def breached_values_from_results(
    results: tuple[NodeResult, ...],
    level: str,
    *,
    node_type: str | None = None,
) -> tuple[str, ...]:
    values: set[str] = set()
    for result in results:
        if node_type is not None and result.node_type != node_type:
            continue
        for scope in result.breached_scopes:
            scoped = scope.levels.get(level)
            if scoped:
                values.update(scoped)
        for scope_levels in result.downstream_hints.get("breached_scope_levels") or []:
            scoped = scope_levels.get(level)
            if scoped:
                values.update(scoped)
    return tuple(sorted(values))


def typed_rows(inputs: ResolvedInputs, name: str, expected_type: type) -> tuple:
    if name not in inputs.data:
        raise IndeterminateError(
            f"required input {name!r} missing from ResolvedInputs",
            details={"input": name},
        )
    raw = inputs.data[name]
    rows = tuple(getattr(raw, "rows", raw))
    for row in rows:
        if not isinstance(row, expected_type):
            raise TypeError(
                f"input {name!r} contains row of type {type(row).__name__}; "
                f"expected {expected_type.__name__}"
            )
    return rows


def select_threshold(
    spec_slice: dict[str, Any],
    *,
    scope_levels: dict[str, str] | None = None,
    ubr_level_8: str | None = None,
) -> dict[str, float | None]:
    if "threshold" in spec_slice:
        threshold = spec_slice.get("threshold") or {}
        chosen = {
            "abs": threshold.get("abs"),
            "rel": threshold.get("rel"),
        }
        for key in ("abs", "rel"):
            value = chosen[key]
            if value is not None and value < 0:
                raise ValueError(f"threshold {key!r} must be non-negative, got {value}")
        return chosen

    default = spec_slice.get("threshold_default") or {}
    chosen: dict[str, float | None] = {
        "abs": default.get("abs"),
        "rel": default.get("rel"),
    }

    effective = scope_levels if scope_levels is not None else (
        {"ubr_level_8": ubr_level_8} if ubr_level_8 is not None else None
    )

    if effective:
        for sr in spec_slice.get("scope_rules") or []:
            selector = sr.get("scope_selector") or {}
            if not isinstance(selector, dict) or not selector:
                continue
            if _selector_matches(selector, effective):
                override = sr.get("threshold") or {}
                if "abs" in override:
                    chosen["abs"] = override["abs"]
                if "rel" in override:
                    chosen["rel"] = override["rel"]
                break

    for key in ("abs", "rel"):
        value = chosen[key]
        if value is not None and value < 0:
            raise ValueError(f"threshold {key!r} must be non-negative, got {value}")
    return chosen


def _selector_matches(selector: dict[str, Any], scope_levels: dict[str, str]) -> bool:
    for level, allowed in selector.items():
        if not isinstance(allowed, (list, tuple)) or not allowed:
            continue
        value = scope_levels.get(level)
        if value is None or value not in allowed:
            return False
    return True


def filter_rows_by_scope(rows: tuple, check_scope: dict[str, list[str]] | None) -> tuple:
    if not check_scope:
        return rows
    filtered = []
    for row in rows:
        if row_matches_scope(row, check_scope):
            filtered.append(row)
    return tuple(filtered)


def row_matches_scope(row: Any, scope: dict[str, list[str]]) -> bool:
    for level, allowed in scope.items():
        value = getattr(row, level, None)
        if value is None or value not in set(allowed):
            return False
    return True


def row_hierarchy(row: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("ubr_level_8", "ubr_level_9", "portfolio", "book_id", "trade_id"):
        value = getattr(row, key, None)
        if value is not None:
            out[key] = value
    return out


def scope_key_for_row(row: Any, breach_level: str) -> dict[str, list[str]]:
    hierarchy = row_hierarchy(row)
    if breach_level not in hierarchy:
        raise IndeterminateError(
            f"breach_level {breach_level!r} is absent from row hierarchy",
            details={"breach_level": breach_level, "available_levels": sorted(hierarchy)},
        )
    levels: dict[str, list[str]] = {}
    for key, value in hierarchy.items():
        levels[key] = [value]
        if key == breach_level:
            break
    return levels


def group_rows_by_grain(rows: tuple, check_grain: str) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for row in rows:
        value = getattr(row, check_grain, None)
        if value is None:
            raise IndeterminateError(
                f"check_grain {check_grain!r} is absent from row",
                details={"check_grain": check_grain, "row_type": type(row).__name__},
            )
        groups.setdefault(value, []).append(row)
    return groups
