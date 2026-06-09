from __future__ import annotations

from typing import Any, ClassVar

from arc.clients.reporting import CompletenessExceptionRow, CompletenessSummaryRow
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

COMPLETENESS_SUMMARY = "completeness_summary"
COMPLETENESS_EXCEPTION_REPORT = "completeness_exception_report"


@register
class MissingTradeThresholdGateHandler(CheckHandler):
    check_id: ClassVar[str] = "missing_trade_threshold_gate"
    handler_version: ClassVar[str] = "1.0.0"
    check_grain: ClassVar[str] = "portfolio"

    input_spec: ClassVar[InputSpec] = InputSpec(
        datasets={
            COMPLETENESS_SUMMARY: {},
            COMPLETENESS_EXCEPTION_REPORT: {},
        }
    )

    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        rows = filter_rows_by_scope(
            typed_rows(inputs, COMPLETENESS_SUMMARY, CompletenessSummaryRow),
            spec_slice.get("check_scope") or {},
        )
        trade_rows = filter_rows_by_scope(
            typed_rows(inputs, COMPLETENESS_EXCEPTION_REPORT, CompletenessExceptionRow),
            spec_slice.get("check_scope") or {},
        )

        if not rows:
            raise IndeterminateError(
                "completeness_summary is empty after scope filters; cannot evaluate gate",
                details={"input": COMPLETENESS_SUMMARY},
            )

        check_grain = spec_slice["check_grain"]
        breach_level = spec_slice["breach_level"]
        scopes = group_rows_by_grain(rows, check_grain)
        per_scope_summary: list[dict[str, Any]] = []
        breached_scopes: list[ScopeKey] = []
        missing_counts_in_breach: list[dict[str, Any]] = []
        missing_trades_by_portfolio: dict[str, list[CompletenessExceptionRow]] = {}
        for trade in trade_rows:
            if trade.status != "complete":
                missing_trades_by_portfolio.setdefault(trade.portfolio, []).append(trade)

        for grain_value in sorted(scopes):
            scope_rows = scopes[grain_value]
            representative = scope_rows[0]
            threshold = select_threshold(spec_slice)
            total = sum(r.trade_count_expected for r in scope_rows)
            missing = sum(
                r.trade_count_error_partial
                + r.trade_count_error_full
                + r.trade_count_not_received
                for r in scope_rows
            )
            ratio_pct = (100.0 * missing / total) if total else 0.0
            breached = (
                (threshold.get("abs") is not None and missing >= threshold["abs"])
                or (threshold.get("rel") is not None and ratio_pct >= threshold["rel"])
            )

            per_scope_summary.append(
                {
                    check_grain: grain_value,
                    "hierarchy": row_hierarchy(representative),
                    "scope_levels": scope_key_for_row(representative, breach_level),
                    "missing_count": missing,
                    "total_count": total,
                    "missing_ratio_pct": round(ratio_pct, 6),
                    "thresholds": threshold,
                    "breached": breached,
                }
            )

            if breached:
                breached_scopes.append(ScopeKey(levels=scope_key_for_row(representative, breach_level)))
                for row in scope_rows:
                    missing_counts_in_breach.append(
                        {
                            "book_id": row.book_id,
                            "hierarchy": row_hierarchy(row),
                            "trade_count_error_partial": row.trade_count_error_partial,
                            "trade_count_error_full": row.trade_count_error_full,
                            "trade_count_not_received": row.trade_count_not_received,
                        }
                    )
                for trade in missing_trades_by_portfolio.get(grain_value, []):
                    missing_counts_in_breach.append(
                        {
                            "trade_id": trade.trade_id,
                            "book_id": trade.book_id,
                            "hierarchy": row_hierarchy(trade),
                            "status": trade.status,
                        }
                    )

        total_missing = sum(s["missing_count"] for s in per_scope_summary)
        total_trades = sum(s["total_count"] for s in per_scope_summary)
        overall_ratio = round(100.0 * total_missing / total_trades, 6) if total_trades else 0.0

        evidence_payloads: dict[str, Any] = {
            "per_scope_summary": {
                "scopes": per_scope_summary,
            }
        }
        if missing_counts_in_breach:
            missing_counts_in_breach.sort(
                key=lambda r: (r["hierarchy"].get("portfolio", ""), r["book_id"])
            )
            evidence_payloads["missing_count_set"] = {"books": missing_counts_in_breach}

        downstream_hints: dict[str, Any] = {}
        if breached_scopes:
            downstream_hints["breached_scope_levels"] = [
                s["scope_levels"] for s in per_scope_summary if s["breached"]
            ]
            missing_trade_ids = sorted(
                {
                    row["trade_id"]
                    for row in missing_counts_in_breach
                    if "trade_id" in row
                }
            )
            downstream_hints["missing_trade_ids"] = missing_trade_ids
            downstream_hints["missing_trades"] = [
                {
                    "trade_id": row["trade_id"],
                    "hierarchy": row["hierarchy"],
                    "status": row["status"],
                }
                for row in sorted(
                    (r for r in missing_counts_in_breach if "trade_id" in r),
                    key=lambda r: r["trade_id"],
                )
            ]

        return HandlerOutput(
            status=NodeStatus.FAIL if breached_scopes else NodeStatus.PASS,
            metrics={
                "missing_count": total_missing,
                "total_count": total_trades,
                "missing_ratio_pct": overall_ratio,
            },
            breached_scopes=breached_scopes,
            evidence_payloads=evidence_payloads,
            downstream_hints=downstream_hints,
        )
