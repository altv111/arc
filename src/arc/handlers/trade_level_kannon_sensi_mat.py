from __future__ import annotations

from typing import Any, ClassVar

from arc.clients.reporting import KannonTradeLevelSensiRow
from arc.core.context import ScopeKey
from arc.core.results import HandlerOutput, IndeterminateError, InputSpec, NodeResult, NodeStatus, ResolvedInputs
from arc.handlers._common import (
    UPSTREAM_RESULTS_KEY,
    missing_trade_ids_from_results,
    missing_trades_from_results,
    select_threshold,
    typed_rows,
    upstream_results,
)
from arc.handlers.registry import CheckHandler, register

KANNON_TRADE_LEVEL_SENSI = "kannon_trade_level_sensi"


@register
class TradeLevelKannonSensiMaterialityHandler(CheckHandler):
    check_id: ClassVar[str] = "trade_level_kannon_sensi_mat"
    handler_version: ClassVar[str] = "1.0.0"
    supported_check_grains: ClassVar[set[str]] = {"trade_id"}

    input_spec: ClassVar[InputSpec] = InputSpec(
        datasets={
            UPSTREAM_RESULTS_KEY: {},
            KANNON_TRADE_LEVEL_SENSI: {},
        }
    )

    def plan_inputs(
        self,
        spec_slice: dict[str, Any],
        prior_results: tuple[NodeResult, ...] = (),
    ) -> InputSpec:
        trade_ids = missing_trade_ids_from_results(prior_results)
        requested_trade_ids: tuple[str, ...] | str = trade_ids or "<upstream:missing_trade_ids>"
        params: dict[str, Any] = {
            "trade_ids": requested_trade_ids,
            "risk_type_list": tuple(spec_slice.get("risk_type_list") or ()),
        }
        sensitivity_types = tuple(spec_slice.get("sensitivity_type_list") or ())
        if sensitivity_types:
            params["sensitivity_type_list"] = sensitivity_types
        return InputSpec(
            datasets={
                UPSTREAM_RESULTS_KEY: {},
                KANNON_TRADE_LEVEL_SENSI: params,
            }
        )

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        missing_trades = _missing_trades_from_upstream(inputs)
        if not missing_trades:
            raise IndeterminateError(
                "no missing trade ids found in upstream results",
                details={"required_hint": "missing_trades"},
            )

        risk_type_list = tuple(spec_slice.get("risk_type_list") or ())
        if not risk_type_list:
            raise IndeterminateError(
                "risk_type_list is required for Kannon trade-level sensitivity materiality",
                details={"required_spec_field": "risk_type_list"},
            )

        threshold = select_threshold(spec_slice)
        abs_threshold = threshold.get("abs")
        if abs_threshold is None:
            raise IndeterminateError(
                "absolute threshold is required for Kannon trade-level sensitivity materiality",
                details={"threshold": threshold},
            )

        sensitivity_types = tuple(spec_slice.get("sensitivity_type_list") or ())
        missing_by_id = {row["trade_id"]: row for row in missing_trades}
        rows = [
            row
            for row in typed_rows(inputs, KANNON_TRADE_LEVEL_SENSI, KannonTradeLevelSensiRow)
            if row.trade_id in missing_by_id
            and row.risk_type in risk_type_list
            and (not sensitivity_types or row.sensitivity_type in sensitivity_types)
        ]

        material_rows: list[dict[str, Any]] = []
        breached_scopes_by_hash: dict[str, ScopeKey] = {}
        for row in rows:
            breached = abs(row.amount_eur) >= abs_threshold
            material = {
                "trade_id": row.trade_id,
                "risk_type": row.risk_type,
                "sensitivity_type": row.sensitivity_type,
                "amount_eur": row.amount_eur,
                "threshold_abs": abs_threshold,
                "hierarchy": missing_by_id[row.trade_id]["hierarchy"],
                "breached": breached,
            }
            material_rows.append(material)
            if breached:
                scope = ScopeKey(levels=_scope_levels(material["hierarchy"], spec_slice["breach_level"]))
                breached_scopes_by_hash[scope.canonical_hash()] = scope

        return HandlerOutput(
            status=NodeStatus.FAIL if breached_scopes_by_hash else NodeStatus.PASS,
            metrics={
                "n_missing_trades": len(missing_by_id),
                "n_sensi_rows_examined": len(rows),
                "n_material_trades": sum(1 for row in material_rows if row["breached"]),
            },
            breached_scopes=list(breached_scopes_by_hash.values()),
            evidence_payloads={"kannon_trade_level_sensi_materiality": {"rows": material_rows}},
            downstream_hints={
                "breached_scope_levels": [
                    scope.model_dump(mode="json")["levels"]
                    for scope in breached_scopes_by_hash.values()
                ],
                "material_trade_ids": sorted(row["trade_id"] for row in material_rows if row["breached"]),
            },
        )


def _missing_trades_from_upstream(inputs: ResolvedInputs) -> tuple[dict[str, Any], ...]:
    return missing_trades_from_results(upstream_results(inputs))


def _scope_levels(hierarchy: dict[str, str], breach_level: str) -> dict[str, list[str]]:
    if breach_level not in hierarchy:
        raise IndeterminateError(
            f"breach_level {breach_level!r} missing from upstream trade hierarchy",
            details={"breach_level": breach_level, "available_levels": sorted(hierarchy)},
        )
    levels: dict[str, list[str]] = {}
    for key in ("ubr_level_8", "ubr_level_9", "portfolio", "book_id", "trade_id"):
        value = hierarchy.get(key)
        if value is not None:
            levels[key] = [value]
        if key == breach_level:
            break
    return levels
