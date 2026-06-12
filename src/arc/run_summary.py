from __future__ import annotations

from typing import Any

from arc.runner import RunReport


def render_run_summary(report: RunReport) -> str:
    lines = [
        f"ARC Run Summary | Rule {report.rule_id}",
        f"Run ID      : {report.run_id}",
        f"BA / Date   : {report.ctx.ba} / {report.ctx.business_date.isoformat()}",
        f"Run type    : {report.ctx.run_type}",
        f"Snapshot    : {report.ctx.snapshot_id}",
        f"Status      : {report.status.value}",
        "",
    ]

    gate = _first(report, node_type="gate")
    if gate is not None:
        lines.extend(
            [
                "Gate",
                "----",
                f"Missing trades : {_metric(gate.metrics, 'missing_count')} / {_metric(gate.metrics, 'total_count')}",
                f"Breached scopes: {len(gate.breached_scopes)}",
                "",
            ]
        )

    evaluates = [result for result in report.results if result.node_type == "evaluate"]
    if evaluates:
        lines.extend(["Evaluations", "-----------"])
        for result in evaluates:
            material = result.metrics.get("n_material_trades")
            suffix = f", material_trades={material}" if material is not None else ""
            lines.append(
                f"- {result.check_id}: {result.status.value}, "
                f"breached_scopes={len(result.breached_scopes)}{suffix}"
            )
        lines.append("")

    classifications = _classifications(report)
    decisions = _decisions(report)
    if classifications or decisions:
        lines.extend(["Attribution & Decisions", "-----------------------"])
        by_portfolio = {row.get("portfolio"): row for row in classifications}
        for decision in decisions:
            portfolio = _portfolio_from_decision(decision)
            classification = decision.get("classification")
            row = by_portfolio.get(portfolio) or {}
            material_trade_ids = row.get("material_trade_ids") or []
            missing_count = row.get("n_missing_trades", "?")
            lines.append(
                f"- {portfolio or '(unknown scope)'}: "
                f"{classification or row.get('root_cause') or 'unknown'} -> {decision['decision']} "
                f"(missing={missing_count}, material_trades={len(material_trade_ids)})"
            )
        lines.append("")

    receipt = _correction_receipt(report)
    if receipt:
        lines.extend(
            [
                "Correction",
                "----------",
                f"Client     : {receipt.get('client')}",
                f"Mode       : {receipt.get('mode')}",
                f"Mutation   : {receipt.get('mutation_performed')}",
                f"Intents    : {receipt.get('n_intents')}",
                f"Receipt ID : {receipt.get('receipt_id')}",
                "",
            ]
        )

    if report.artifacts_dir is not None:
        lines.extend(["Artifacts", "---------", str(report.artifacts_dir)])

    return "\n".join(lines)


def _first(report: RunReport, *, node_type: str):
    return next((result for result in report.results if result.node_type == node_type), None)


def _metric(metrics: dict[str, Any], key: str) -> Any:
    return metrics.get(key, "?")


def _classifications(report: RunReport) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in report.results:
        if result.node_type == "attribute":
            rows.extend(result.downstream_hints.get("classifications") or [])
    return rows


def _decisions(report: RunReport) -> list[dict[str, Any]]:
    for result in report.results:
        if result.node_type == "decide":
            return list(result.downstream_hints.get("decisions") or [])
    return []


def _correction_receipt(report: RunReport) -> dict[str, Any]:
    for result in report.results:
        if result.node_type == "act":
            return dict(result.downstream_hints.get("correction_receipt") or {})
    return {}


def _portfolio_from_decision(decision: dict[str, Any]) -> str | None:
    for scope in decision.get("breached_scope_levels") or []:
        portfolio = scope.get("portfolio")
        if portfolio:
            return portfolio[0]
    return None
