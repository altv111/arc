from __future__ import annotations

from collections import defaultdict
from typing import Any, ClassVar

from arc.clients.reporting import (
    HistoricalTradeStatusRow,
    RiskfinderCalcStatusRow,
    UpstreamTradePresenceRow,
)
from arc.core.results import HandlerOutput, InputSpec, NodeResult, NodeStatus, ResolvedInputs
from arc.handlers._common import (
    UPSTREAM_RESULTS_KEY,
    missing_trade_ids_from_results,
    missing_trades_from_results,
    typed_rows,
    upstream_results,
)
from arc.handlers.registry import CheckHandler, register

RISKFINDER_CALC_STATUS = "riskfinder_calc_status"
HISTORICAL_TRADE_STATUS = "historical_trade_status"
UPSTREAM_TRADE_PRESENCE = "upstream_trade_presence"


@register
class AttributeMissingTradesHandler(CheckHandler):
    check_id: ClassVar[str] = "attribute_missing_trades"
    handler_version: ClassVar[str] = "1.0.0"

    input_spec: ClassVar[InputSpec] = InputSpec(
        datasets={
            UPSTREAM_RESULTS_KEY: {},
            RISKFINDER_CALC_STATUS: {},
            HISTORICAL_TRADE_STATUS: {},
            UPSTREAM_TRADE_PRESENCE: {},
        }
    )

    def plan_inputs(
        self,
        spec_slice: dict[str, Any],
        prior_results: tuple[NodeResult, ...] = (),
    ) -> InputSpec:
        trade_ids = missing_trade_ids_from_results(prior_results)
        requested_trade_ids: tuple[str, ...] | str = trade_ids or "<upstream:missing_trade_ids>"
        params = {"trade_ids": requested_trade_ids}
        return InputSpec(
            datasets={
                UPSTREAM_RESULTS_KEY: {},
                RISKFINDER_CALC_STATUS: params,
                HISTORICAL_TRADE_STATUS: params,
                UPSTREAM_TRADE_PRESENCE: params,
            }
        )

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        results = upstream_results(inputs)
        missing_trades = missing_trades_from_results(results)
        material_trade_ids = _material_trade_ids(results)
        riskfinder_by_trade = {
            row.trade_id: row
            for row in typed_rows(inputs, RISKFINDER_CALC_STATUS, RiskfinderCalcStatusRow)
        }
        historical_by_trade = {
            row.trade_id: row
            for row in typed_rows(inputs, HISTORICAL_TRADE_STATUS, HistoricalTradeStatusRow)
        }
        upstream_by_trade = {
            row.trade_id: row
            for row in typed_rows(inputs, UPSTREAM_TRADE_PRESENCE, UpstreamTradePresenceRow)
        }
        by_portfolio = _group_missing_trades(
            missing_trades,
            material_trade_ids,
            riskfinder_by_trade,
            historical_by_trade,
            upstream_by_trade,
        )

        classifications = []
        for portfolio in sorted(by_portfolio):
            row = by_portfolio[portfolio]
            root_cause = _classify_portfolio(row["trade_classifications"])
            classifications.append(
                {
                    **row,
                    "classifier": spec_slice.get("classifier") or "missing_trades",
                    "root_cause": root_cause,
                    "late_arrival_possible": root_cause == "expected_to_arrive_late",
                    "recommended_follow_up": _follow_up_for(root_cause),
                }
            )

        attributed_breaches = [
            {
                "node_id": spec_slice["row_id"],
                "check_id": self.check_id,
                "node_type": "attribute",
                "breached_scope_levels": [row["scope_levels"]],
                "metrics": {
                    "n_missing_trades": row["n_missing_trades"],
                    "n_material_trades": row["n_material_trades"],
                },
                "classification": row["root_cause"],
                "late_arrival_possible": row["late_arrival_possible"],
                "recommended_decision": _recommended_decision_for(row["root_cause"]),
            }
            for row in classifications
            if row["n_material_trades"] > 0
        ]

        return HandlerOutput(
            status=NodeStatus.PASS,
            metrics={
                "n_portfolios_classified": len(classifications),
                "n_material_portfolios": len(attributed_breaches),
            },
            evidence_payloads={"missing_trade_attribution": {"classifications": classifications}},
            downstream_hints={
                "attribution_classifier": spec_slice.get("classifier") or "missing_trades",
                "classifications": classifications,
                "attributed_breaches": attributed_breaches,
            },
        )


def _material_trade_ids(results: tuple) -> set[str]:
    ids: set[str] = set()
    for result in results:
        if result.node_type != "evaluate":
            continue
        ids.update(result.downstream_hints.get("material_trade_ids") or [])
    return ids


def _group_missing_trades(
    missing_trades: tuple[dict[str, Any], ...],
    material_trade_ids: set[str],
    riskfinder_by_trade: dict[str, RiskfinderCalcStatusRow],
    historical_by_trade: dict[str, HistoricalTradeStatusRow],
    upstream_by_trade: dict[str, UpstreamTradePresenceRow],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    trade_statuses: dict[str, set[str]] = defaultdict(set)
    for trade in missing_trades:
        hierarchy = trade["hierarchy"]
        portfolio = hierarchy["portfolio"]
        item = grouped.setdefault(
            portfolio,
            {
                "portfolio": portfolio,
                "scope_levels": _scope_levels(hierarchy, "portfolio"),
                "missing_trade_ids": [],
                "material_trade_ids": [],
                "statuses": [],
                "trade_classifications": [],
                "n_missing_trades": 0,
                "n_material_trades": 0,
            },
        )
        item["missing_trade_ids"].append(trade["trade_id"])
        item["n_missing_trades"] += 1
        trade_statuses[portfolio].add(trade.get("status") or "unknown")
        item["trade_classifications"].append(
            _classify_trade(
                trade["trade_id"],
                riskfinder_by_trade.get(trade["trade_id"]),
                historical_by_trade.get(trade["trade_id"]),
                upstream_by_trade.get(trade["trade_id"]),
            )
        )
        if trade["trade_id"] in material_trade_ids:
            item["material_trade_ids"].append(trade["trade_id"])
            item["n_material_trades"] += 1

    for portfolio, item in grouped.items():
        item["missing_trade_ids"].sort()
        item["material_trade_ids"].sort()
        item["trade_classifications"].sort(key=lambda row: row["trade_id"])
        item["statuses"] = sorted(trade_statuses[portfolio])
    return grouped


def _scope_levels(hierarchy: dict[str, str], breach_level: str) -> dict[str, list[str]]:
    levels: dict[str, list[str]] = {}
    for key in ("ubr_level_8", "ubr_level_9", "portfolio", "book_id", "trade_id"):
        value = hierarchy.get(key)
        if value is not None:
            levels[key] = [value]
        if key == breach_level:
            break
    return levels


def _classify_trade(
    trade_id: str,
    riskfinder: RiskfinderCalcStatusRow | None,
    historical: HistoricalTradeStatusRow | None,
    upstream: UpstreamTradePresenceRow | None,
) -> dict[str, Any]:
    riskfinder_status = riskfinder.status if riskfinder is not None else "unknown"
    present_in_upstream = upstream.present_in_upstream if upstream is not None else None
    historically_arrives_late = (
        historical.historically_arrives_late if historical is not None else None
    )
    expected_arrival_time_ist = (
        historical.expected_arrival_time_ist if historical is not None else None
    )

    if riskfinder_status == "errored":
        root_cause = "arrived_but_errored"
    elif riskfinder_status == "successful":
        root_cause = "defect_green_status_but_missing"
    elif riskfinder_status in {"not_arrived", "pending"}:
        if present_in_upstream is False:
            root_cause = "missing_in_upstream"
        elif present_in_upstream is True and historically_arrives_late is True:
            root_cause = "expected_to_arrive_late"
        else:
            root_cause = "defect_not_arrived_without_late_pattern"
    else:
        root_cause = "unknown"

    return {
        "trade_id": trade_id,
        "riskfinder_status": riskfinder_status,
        "present_in_upstream": present_in_upstream,
        "historically_arrives_late": historically_arrives_late,
        "expected_arrival_time_ist": expected_arrival_time_ist,
        "root_cause": root_cause,
    }


def _classify_portfolio(trade_classifications: list[dict[str, Any]]) -> str:
    causes = {row["root_cause"] for row in trade_classifications}
    if not causes:
        return "unknown"
    if causes == {"expected_to_arrive_late"}:
        return "expected_to_arrive_late"
    if "arrived_but_errored" in causes:
        return "arrived_but_errored"
    if "missing_in_upstream" in causes:
        return "missing_in_upstream"
    if len(causes) == 1:
        return next(iter(causes))
    return "mixed_missing_trade_root_cause"


def _follow_up_for(root_cause: str) -> str:
    if root_cause == "arrived_but_errored":
        return "raise RiskFinder investigation record"
    if root_cause == "expected_to_arrive_late":
        return "hold and recheck before flash deadline"
    if root_cause == "missing_in_upstream":
        return "raise upstream missing trade investigation"
    return "manual review required"


def _recommended_decision_for(root_cause: str) -> str:
    if root_cause == "expected_to_arrive_late":
        return "hold"
    return "roll"
