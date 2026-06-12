from __future__ import annotations

import json
import re
from io import StringIO
from typing import Any

from arc.nodes.base import Node
from arc.rule import Rule
from arc.core.results import IndeterminateError
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

LANES = ("gate", "evaluate", "attribute", "decide", "act", "record")
INTERNAL_DATASETS = frozenset({"_upstream_results"})


def render_rule_mermaid(rule: Rule) -> str:
    lines = [
        "flowchart LR",
        f"  title[Rule {rule.row_id}: {getattr(rule.spec, 'breach', 'ARC Rule')}]",
        "",
    ]

    for lane in LANES:
        nodes = rule.nodes_of_type(lane)
        if not nodes:
            continue
        lines.append(f"  subgraph {lane}[{lane}]")
        for node in nodes:
            lines.append(f"    {node_ref(node)}[{node_label(node)}]")
        lines.append("  end")
        lines.append("")

    for edge in compute_edges(rule):
        lines.append(f"  {node_ref(edge[0])} --> {node_ref(edge[1])}")

    lines.append("")
    lines.append("  classDef gate fill:#f6f8fa,stroke:#57606a,color:#24292f;")
    lines.append("  classDef evaluate fill:#fff8c5,stroke:#9a6700,color:#24292f;")
    lines.append("  classDef attribute fill:#dafbe1,stroke:#1a7f37,color:#24292f;")
    lines.append("  classDef decide fill:#ddf4ff,stroke:#0969da,color:#24292f;")
    lines.append("  classDef act fill:#fbefff,stroke:#8250df,color:#24292f;")
    lines.append("  classDef record fill:#ffebe9,stroke:#cf222e,color:#24292f;")
    for node in rule.nodes:
        lines.append(f"  class {node_ref(node)} {node.node_type};")

    return "\n".join(lines)


def render_rule_text(rule: Rule) -> str:
    lines = [
        f"Rule {rule.row_id}: {getattr(rule.spec, 'breach', 'ARC Rule')}",
        f"parent_scope: {rule.parent_scope}",
        "",
    ]
    for lane in LANES:
        nodes = rule.nodes_of_type(lane)
        if not nodes:
            continue
        lines.append(lane)
        for node in nodes:
            lines.append(f"  - {node.node_id}")
            lines.append(f"    handler: {node.check_id}")
            spec = spec_slice(node)
            if "check_scope" in spec:
                lines.append(f"    check_scope: {spec.get('check_scope') or {}}")
            if "check_grain" in spec:
                lines.append(f"    check_grain: {spec['check_grain']}")
            if "breach_level" in spec:
                lines.append(f"    breach_level: {spec['breach_level']}")
            if "threshold" in spec:
                lines.append(f"    threshold: {spec['threshold']}")
        lines.append("")
    lines.append("edges")
    for source, target in compute_edges(rule):
        lines.append(f"  {source.node_id} -> {target.node_id}")
    return "\n".join(lines)


def render_rule_plan(rule: Rule) -> str:
    lines = [
        f"ARC Plan | Rule {rule.row_id}: {getattr(rule.spec, 'breach', 'ARC Rule')}",
        f"Parent scope: {_short_dict(rule.parent_scope)}",
        f"Gate policy: {rule.gate_logic}",
        "",
        "Lane plan",
        "---------",
    ]

    for lane in LANES:
        nodes = rule.nodes_of_type(lane)
        if not nodes:
            continue
        lines.append(f"{lane.upper()}")
        for node in nodes:
            spec = spec_slice(node)
            lines.append(f"  {node.node_id}")
            lines.append(f"    handler    : {node.check_id}")
            lines.append(f"    datasets   : {_datasets(node)}")
            if "check_scope" in spec:
                check_scope = spec.get("check_scope") or {}
                where = "parent_scope" if not check_scope else f"parent_scope + {_short_dict(check_scope)}"
                lines.append(f"    where      : {where}")
            elif lane in {"attribute", "record"} and "parent_scope" in spec:
                lines.append(f"    where      : {_short_dict(spec['parent_scope'])}")
            else:
                lines.append("    where      : upstream results")
            if "check_grain" in spec:
                lines.append(f"    group/eval : {spec['check_grain']}")
            if "breach_level" in spec:
                lines.append(f"    emits      : breached {spec['breach_level']}")
            elif lane == "attribute":
                lines.append("    emits      : attributed breaches")
            elif lane == "decide":
                lines.append("    emits      : correction decisions")
            elif lane == "act":
                lines.append("    emits      : correction intents + correction receipt")
            elif lane == "record":
                lines.append("    emits      : canonical breach record")
            if "threshold" in spec:
                lines.append(f"    threshold  : {_short_dict(spec['threshold'])}")
            if "decision_options" in spec:
                lines.append(f"    options    : {', '.join(spec['decision_options'])}")
            if "mode" in spec:
                lines.append(f"    mode       : {spec['mode']}")
        lines.append("")

    lines.append("Path view")
    lines.append("---------")
    for source, target in compute_edges(rule):
        lines.append(f"{source.node_id} -> {target.node_id}")
    lines.extend(["", *render_dataset_contract_lines(rule)])
    return "\n".join(lines)


def render_dataset_contract(rule: Rule) -> str:
    return "\n".join(render_dataset_contract_lines(rule))


def render_dataset_contract_json(rule: Rule) -> str:
    return json.dumps(dataset_contract_payload(rule), indent=2, sort_keys=True)


def dataset_contract_payload(rule: Rule) -> dict[str, Any]:
    return {
        "rule_id": rule.row_id,
        "breach": getattr(rule.spec, "breach", "ARC Rule"),
        "parent_scope": rule.parent_scope,
        "scope_note": "parent_scope is applied centrally before handler evaluation",
        "datasets": dataset_contract(rule),
    }


def render_dataset_contract_lines(rule: Rule) -> list[str]:
    entries = dataset_contract(rule)
    lines = [
        "Dataset contract",
        "----------------",
        "Scope: parent_scope is applied centrally before handler evaluation.",
    ]
    if not entries:
        lines.append("(no external reporting datasets required)")
        return lines

    for entry in entries:
        lines.append(f"- {entry['dataset']}")
        lines.append(f"  requested_by: {', '.join(entry['requested_by'])}")
        lines.append(f"  params      : {_short_dict(entry['params']) if entry['params'] else '{}'}")
    return lines


def dataset_contract(rule: Rule) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for node in rule.nodes:
        input_spec = _input_spec_for_node(node)
        for dataset, params in input_spec.datasets.items():
            if dataset in INTERNAL_DATASETS:
                continue
            key = (dataset, _short_dict(params))
            entry = grouped.setdefault(
                key,
                {
                    "dataset": dataset,
                    "params": dict(params),
                    "requested_by": [],
                },
            )
            entry["requested_by"].append(node.node_id)

    return sorted(
        grouped.values(),
        key=lambda item: (item["dataset"], _short_dict(item["params"])),
    )


def render_rule_rich(rule: Rule, *, width: int = 120) -> str:
    console = Console(
        width=width,
        record=True,
        file=StringIO(),
        force_terminal=True,
        color_system="standard",
    )
    console.print(_rich_header(rule))

    for lane in LANES:
        nodes = rule.nodes_of_type(lane)
        if nodes:
            console.print(_rich_lane_panel(lane, nodes))

    console.print(_rich_path_panel(rule))
    return console.export_text(styles=True)


def compute_edges(rule: Rule) -> list[tuple[Node, Node]]:
    edges: list[tuple[Node, Node]] = []
    gates = rule.nodes_of_type("gate")
    evaluates = rule.nodes_of_type("evaluate")
    attributes = rule.nodes_of_type("attribute")
    decides = rule.nodes_of_type("decide")
    acts = rule.nodes_of_type("act")
    records = rule.nodes_of_type("record")

    for gate in gates:
        for evaluate in evaluates:
            edges.append((gate, evaluate))

    upstream_compute = evaluates or gates
    for source in upstream_compute:
        for attribute in attributes:
            edges.append((source, attribute))

    for attribute in attributes:
        for decide in decides:
            edges.append((attribute, decide))

    for decide in decides:
        for act in acts:
            edges.append((decide, act))

    for act in acts:
        for record in records:
            edges.append((act, record))

    return edges


def node_ref(node: Node) -> str:
    return "n_" + re.sub(r"[^a-zA-Z0-9_]", "_", node.node_id)


def node_label(node: Node) -> str:
    spec = spec_slice(node)
    parts = [
        _escape_label(node.node_id),
        f"handler: {_escape_label(node.check_id)}",
    ]
    if "check_scope" in spec:
        parts.append(f"scope: {_escape_label(_short_dict(spec.get('check_scope') or {}))}")
    if "check_grain" in spec:
        parts.append(f"grain: {_escape_label(str(spec['check_grain']))}")
    if "breach_level" in spec:
        parts.append(f"breach: {_escape_label(str(spec['breach_level']))}")
    if "threshold" in spec:
        parts.append(f"threshold: {_escape_label(_short_dict(spec['threshold']))}")
    return '"{}"'.format("<br/>".join(parts))


def spec_slice(node: Node) -> dict[str, Any]:
    return dict(getattr(node, "_spec_slice"))  # noqa: SLF001


def _datasets(node: Node) -> str:
    input_spec = _input_spec_for_node(node)
    datasets = [
        _dataset_label(name, input_spec.datasets[name])
        for name in sorted((input_spec.datasets or {}).keys())
    ]
    return ", ".join(datasets) if datasets else "(none)"


def _input_spec_for_node(node: Node):
    handler = getattr(node, "_handler")  # noqa: SLF001
    try:
        return handler.plan_inputs(_planning_spec_for_visual(node), ())
    except IndeterminateError:
        return handler.input_spec


def _planning_spec_for_visual(node: Node) -> dict[str, Any]:
    spec = spec_slice(node)
    spec.setdefault("__run_type", "<runtime>")
    return spec


def _dataset_label(name: str, params: dict[str, Any]) -> str:
    if not params:
        return name
    return f"{name}({_short_dict(params)})"


def _rich_header(rule: Rule) -> Panel:
    title = Text(f"ARC Rule Plan | Rule {rule.row_id}: {getattr(rule.spec, 'breach', 'ARC Rule')}", style="bold white")
    body = Table.grid(padding=(0, 2))
    body.add_column(style="bold cyan")
    body.add_column()
    body.add_row("Parent scope", _short_dict(rule.parent_scope))
    body.add_row("Gate policy", rule.gate_logic)
    body.add_row("Nodes", str(len(rule.nodes)))
    return Panel(Group(title, body), border_style="bright_blue", box=box.ROUNDED)


def _rich_lane_panel(lane: str, nodes: list[Node]) -> Panel:
    cards = []
    for node in nodes:
        spec = spec_slice(node)
        tree = Tree(f"[bold]{node.node_id}[/bold]")
        tree.add(f"[cyan]handler[/cyan]: {node.check_id}")
        tree.add(f"[magenta]datasets[/magenta]: {_datasets(node)}")
        tree.add(f"[green]scope/inputs[/green]: {_scope_summary(lane, spec)}")
        tree.add(f"[yellow]eval[/yellow]: {_eval_summary(spec)}")
        tree.add(f"[bright_blue]emits[/bright_blue]: {_emit_summary(lane, spec)}")
        cards.append(tree)

    return Panel(Group(*cards), title=lane.upper(), border_style=_lane_style(lane), box=box.ROUNDED)


def _rich_path_panel(rule: Rule) -> Panel:
    tree = Tree("[bold]Compute and decision paths[/bold]")
    by_source: dict[str, list[Node]] = {}
    node_by_id = {node.node_id: node for node in rule.nodes}
    for source, target in compute_edges(rule):
        by_source.setdefault(source.node_id, []).append(target)

    roots = rule.nodes_of_type("gate") or rule.nodes_of_type("evaluate")
    for root in roots:
        _add_path_tree(tree, root, by_source, node_by_id, seen=())
    return Panel(tree, title="PATH VIEW", border_style="bright_black", box=box.ROUNDED)


def _add_path_tree(
    tree: Tree,
    node: Node,
    by_source: dict[str, list[Node]],
    node_by_id: dict[str, Node],
    *,
    seen: tuple[str, ...],
) -> None:
    branch = tree.add(f"[{_lane_style(node.node_type)}]{node.node_id}[/{_lane_style(node.node_type)}]")
    if node.node_id in seen:
        branch.add("[red]cycle detected[/red]")
        return
    for target in by_source.get(node.node_id, []):
        _add_path_tree(branch, node_by_id[target.node_id], by_source, node_by_id, seen=(*seen, node.node_id))


def _scope_summary(lane: str, spec: dict[str, Any]) -> str:
    if "check_scope" in spec:
        check_scope = spec.get("check_scope") or {}
        return "parent_scope" if not check_scope else f"parent_scope + {_short_dict(check_scope)}"
    if lane in {"attribute", "record"} and "parent_scope" in spec:
        return _short_dict(spec["parent_scope"])
    return "upstream results"


def _eval_summary(spec: dict[str, Any]) -> str:
    parts = []
    if "check_grain" in spec:
        parts.append(f"grain={spec['check_grain']}")
    if "threshold" in spec:
        parts.append(f"threshold={_short_dict(spec['threshold'])}")
    if "decision_options" in spec:
        parts.append("options=" + ", ".join(spec["decision_options"]))
    if "mode" in spec:
        parts.append(f"mode={spec['mode']}")
    return "\n".join(parts) if parts else "-"


def _emit_summary(lane: str, spec: dict[str, Any]) -> str:
    if "breach_level" in spec:
        return f"breached {spec['breach_level']}"
    if lane == "attribute":
        return "attributed breaches"
    if lane == "decide":
        return "correction decisions"
    if lane == "act":
        return "correction intents\ncorrection receipt"
    if lane == "record":
        return "canonical breach record"
    return "-"


def _lane_style(lane: str) -> str:
    return {
        "gate": "bright_white",
        "evaluate": "yellow",
        "attribute": "green",
        "decide": "cyan",
        "act": "magenta",
        "record": "red",
    }.get(lane, "white")


def _short_dict(value: dict[str, Any]) -> str:
    if not value:
        return "{}"
    parts = []
    for key, raw in value.items():
        if isinstance(raw, (list, tuple)):
            rendered = ",".join(str(item) for item in raw)
        else:
            rendered = str(raw)
        parts.append(f"{key}={rendered}")
    return "; ".join(parts)


def _escape_label(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "'")
