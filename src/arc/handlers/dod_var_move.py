from __future__ import annotations

from typing import Any, ClassVar

from arc.clients.reporting import DodVarExtractRow
from arc.core.context import ScopeKey
from arc.core.results import HandlerOutput, IndeterminateError, InputSpec, NodeResult, NodeStatus, ResolvedInputs
from arc.handlers._common import (
    breached_values_from_results,
    filter_rows_by_scope,
    group_rows_by_grain,
    row_hierarchy,
    scope_key_for_row,
    select_threshold,
    typed_rows,
)
from arc.handlers.registry import CheckHandler, register

DOD_VAR_EXTRACT = "dod_var_extract"


@register
class DodVarMoveHandler(CheckHandler):
    check_id: ClassVar[str] = "dod_var_move"
    handler_version: ClassVar[str] = "1.0.0"
    supported_check_grains: ClassVar[set[str]] = {"portfolio", "ubr_level_9"}

    def plan_inputs(
        self,
        spec_slice: dict[str, Any],
        prior_results: tuple[NodeResult, ...] = (),
    ) -> InputSpec:
        params: dict[str, Any] = {
            "grain": spec_slice["check_grain"],
            "var_type": "TOTAL",
            "fields": _fields_for_run_type(str(spec_slice["__run_type"]).lower()),
        }
        if spec_slice["check_grain"] == "portfolio":
            portfolios = breached_values_from_results(prior_results, "portfolio", node_type="gate")
            params["portfolio_names"] = portfolios or "<upstream:gate_breached_portfolios>"
        return InputSpec(
            datasets={
                DOD_VAR_EXTRACT: params
            }
        )

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        rows = filter_rows_by_scope(
            typed_rows(inputs, DOD_VAR_EXTRACT, DodVarExtractRow),
            spec_slice.get("check_scope") or {},
        )
        if not rows:
            raise IndeterminateError(
                "dod_var_extract is empty after scope filters; cannot evaluate DoD VaR move",
                details={"input": DOD_VAR_EXTRACT},
            )

        check_grain = spec_slice["check_grain"]
        breach_level = spec_slice["breach_level"]
        threshold = select_threshold(spec_slice)
        run_type = str(spec_slice["__run_type"]).lower()
        scopes = group_rows_by_grain(rows, check_grain)

        per_scope: list[dict[str, Any]] = []
        breached_scopes: list[ScopeKey] = []

        for grain_value in sorted(scopes):
            scope_rows = scopes[grain_value]
            representative = scope_rows[0]
            curr_var = sum(_var_values(row, run_type)[0] for row in scope_rows)
            prev_var = sum(_var_values(row, run_type)[1] for row in scope_rows)
            diff_pct = _diff_pct(curr_var, prev_var)
            abs_move = abs(curr_var - prev_var)
            breached = (
                (threshold.get("abs") is not None and abs_move >= threshold["abs"])
                or (threshold.get("rel") is not None and diff_pct is not None and abs(diff_pct) >= threshold["rel"])
            )

            per_scope.append(
                {
                    check_grain: grain_value,
                    "hierarchy": row_hierarchy(representative),
                    "scope_levels": scope_key_for_row(representative, breach_level),
                    "curr_var": curr_var,
                    "prev_var": prev_var,
                    "abs_move": abs_move,
                    "diff_pct": diff_pct,
                    "thresholds": threshold,
                    "breached": breached,
                }
            )
            if breached:
                breached_scopes.append(ScopeKey(levels=scope_key_for_row(representative, breach_level)))

        return HandlerOutput(
            status=NodeStatus.FAIL if breached_scopes else NodeStatus.PASS,
            metrics={
                "n_breached_scopes": len(breached_scopes),
                "n_scopes_examined": len(per_scope),
            },
            breached_scopes=breached_scopes,
            evidence_payloads={"dod_var_move_per_scope": {"scopes": per_scope}},
            downstream_hints={
                "breached_scope_levels": [s["scope_levels"] for s in per_scope if s["breached"]]
            }
        )


def _var_values(row: DodVarExtractRow, run_type: str) -> tuple[float, float]:
    if run_type == "1dvar":
        return row.curr_1d_var, row.prev_1d_var
    if run_type == "10dvar":
        return row.curr_10d_var, row.prev_10d_var
    if run_type == "10dsvar":
        return row.curr_10d_svar, row.prev_10d_svar
    raise IndeterminateError(
        "unsupported run_type for DoD VaR move",
        details={"run_type": run_type, "supported": ["1dvar", "10dvar", "10dsvar"]},
    )


def _diff_pct(curr_var: float, prev_var: float) -> float | None:
    if prev_var == 0:
        return None
    return round(100.0 * (curr_var - prev_var) / abs(prev_var), 6)


def _fields_for_run_type(run_type: str) -> tuple[str, str]:
    if run_type == "<runtime>":
        return "<runtime_curr_var>", "<runtime_prev_var>"
    if run_type == "1dvar":
        return "Curr1DVaR", "Prev1DVAR"
    if run_type == "10dvar":
        return "Curr10DVaR", "Prev10DVAR"
    if run_type == "10dsvar":
        return "Curr1DSVaR", "Prev1DSVAR"
    raise IndeterminateError(
        "unsupported run_type for DoD VaR move input planning",
        details={"run_type": run_type, "supported": ["1dvar", "10dvar", "10dsvar"]},
    )
