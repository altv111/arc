from __future__ import annotations

from collections import defaultdict
from typing import Any, ClassVar

from arc.core.results import HandlerOutput, InputSpec, NodeStatus, ResolvedInputs
from arc.handlers._common import UPSTREAM_RESULTS_KEY, missing_trades_from_results, upstream_results
from arc.handlers.registry import CheckHandler, register


@register
class AttributeMissingTradesHandler(CheckHandler):
    check_id: ClassVar[str] = "attribute_missing_trades"
    handler_version: ClassVar[str] = "1.0.0"

    input_spec: ClassVar[InputSpec] = InputSpec(datasets={UPSTREAM_RESULTS_KEY: {}})

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        results = upstream_results(inputs)
        missing_trades = missing_trades_from_results(results)
        material_trade_ids = _material_trade_ids(results)
        by_portfolio = _group_missing_trades(missing_trades, material_trade_ids)

        classifications = []
        for portfolio in sorted(by_portfolio):
            row = by_portfolio[portfolio]
            statuses = set(row["statuses"])
            root_cause = _classify_missing_trade_statuses(statuses)
            classifications.append(
                {
                    **row,
                    "classifier": spec_slice.get("classifier") or "missing_trades",
                    "root_cause": root_cause,
                    "late_arrival_possible": root_cause not in {"riskfinder_error"},
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
                "n_missing_trades": 0,
                "n_material_trades": 0,
            },
        )
        item["missing_trade_ids"].append(trade["trade_id"])
        item["n_missing_trades"] += 1
        trade_statuses[portfolio].add(trade.get("status") or "unknown")
        if trade["trade_id"] in material_trade_ids:
            item["material_trade_ids"].append(trade["trade_id"])
            item["n_material_trades"] += 1

    for portfolio, item in grouped.items():
        item["missing_trade_ids"].sort()
        item["material_trade_ids"].sort()
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


def _classify_missing_trade_statuses(statuses: set[str]) -> str:
    if statuses == {"riskfinder_error"}:
        return "riskfinder_error"
    if statuses and statuses <= {"not_received", "missing", "never_arrived"}:
        return "never_arrived"
    if not statuses:
        return "unknown"
    return "mixed_missing_trade_status"


def _follow_up_for(root_cause: str) -> str:
    if root_cause == "riskfinder_error":
        return "raise RiskFinder investigation record"
    if root_cause == "never_arrived":
        return "check upstream arrival and late-delivery windows"
    return "manual review required"
