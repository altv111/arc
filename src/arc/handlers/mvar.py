from __future__ import annotations

from typing import Any, ClassVar

from arc.clients.reporting import MVarRow
from arc.core.context import ScopeKey
from arc.core.results import HandlerOutput, IndeterminateError, InputSpec, NodeStatus, ResolvedInputs
from arc.handlers._common import (
    filter_rows_by_scope,
    group_rows_by_grain,
    row_hierarchy,
    scope_key_for_row,
    select_threshold,
    typed_rows,
)
from arc.handlers.registry import CheckHandler, register

MVAR = "mvar"


@register
class MVarHandler(CheckHandler):
    check_id: ClassVar[str] = "mvar"
    handler_version: ClassVar[str] = "1.0.0"
    check_grain: ClassVar[str] = "portfolio"

    input_spec: ClassVar[InputSpec] = InputSpec(datasets={MVAR: {}})

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        rows = filter_rows_by_scope(
            typed_rows(inputs, MVAR, MVarRow),
            spec_slice.get("check_scope") or {},
        )
        if not rows:
            raise IndeterminateError(
                "mvar is empty after scope filters; cannot evaluate MVaR materiality",
                details={"input": MVAR},
            )

        check_grain = spec_slice["check_grain"]
        breach_level = spec_slice["breach_level"]
        scopes = group_rows_by_grain(rows, check_grain)

        per_scope: list[dict[str, Any]] = []
        breached_scopes: list[ScopeKey] = []
        any_breach = False
        contributing_rows = 0

        for grain_value in sorted(scopes):
            scope_rows = scopes[grain_value]
            representative = scope_rows[0]
            usable_mvars = [r.mvar for r in scope_rows if r.mvar is not None]
            contributing_rows += len(usable_mvars)
            parent_candidates = [r.parent_var for r in scope_rows if r.parent_var is not None]
            parent_var_scope = max(parent_candidates) if parent_candidates else None

            mvar_sum = sum(usable_mvars) if usable_mvars else None
            mvar_pct: float | None = None
            if mvar_sum is not None and parent_var_scope:
                mvar_pct = round(100.0 * mvar_sum / abs(parent_var_scope), 6)

            threshold = select_threshold(spec_slice)

            breached = False
            if threshold.get("abs") is not None and mvar_sum is not None:
                breached = breached or abs(mvar_sum) >= threshold["abs"]
            if threshold.get("rel") is not None and mvar_pct is not None:
                breached = breached or abs(mvar_pct) >= threshold["rel"]

            per_scope.append(
                {
                    check_grain: grain_value,
                    "hierarchy": row_hierarchy(representative),
                    "scope_levels": scope_key_for_row(representative, breach_level),
                    "n_rows": len(scope_rows),
                    "n_usable_rows": len(usable_mvars),
                    "mvar_sum": mvar_sum,
                    "parent_var": parent_var_scope,
                    "mvar_pct": mvar_pct,
                    "thresholds": threshold,
                    "breached": breached,
                }
            )

            if breached:
                any_breach = True
                breached_scopes.append(ScopeKey(levels=scope_key_for_row(representative, breach_level)))

        if contributing_rows == 0:
            raise IndeterminateError(
                "no MVAR row carries an mvar value; cannot evaluate",
                details={"input": MVAR, "rows_examined": len(rows)},
            )

        downstream_hints: dict[str, Any] = {}
        if any_breach:
            downstream_hints["breached_scope_levels"] = [
                s["scope_levels"] for s in per_scope if s["breached"]
            ]

        return HandlerOutput(
            status=NodeStatus.FAIL if any_breach else NodeStatus.PASS,
            metrics={
                "n_breached_scopes": len(breached_scopes),
                "n_scopes_examined": len(per_scope),
            },
            breached_scopes=breached_scopes,
            evidence_payloads={"mvar_per_scope": {"scopes": per_scope}},
            downstream_hints=downstream_hints,
        )
