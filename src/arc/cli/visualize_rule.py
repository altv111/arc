from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import arc.handlers  # noqa: F401 - import-time registration
from arc.core.evidence_store import EvidenceStore
from arc.core.run_state import RunStateStore
from arc.rule import build_rule_from_json
from arc.visualize import (
    render_dataset_contract,
    render_dataset_contract_json,
    render_rule_mermaid,
    render_rule_plan,
    render_rule_rich,
    render_rule_text,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize ARC rule lanes and node paths.")
    parser.add_argument(
        "rule_json",
        nargs="?",
        default="fixtures/rules/row1.json",
        help="Path to a rule JSON file.",
    )
    parser.add_argument(
        "--format",
        choices=("rich", "mermaid", "plan", "text", "datasets", "datasets-json"),
        default="rich",
        help="Output format.",
    )
    args = parser.parse_args()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        rule = build_rule_from_json(
            Path(args.rule_json),
            evidence_store=EvidenceStore(root / "evidence"),
            run_state_store=RunStateStore(root / "state"),
        )

    if args.format == "rich":
        print(render_rule_rich(rule), end="")
    elif args.format == "datasets":
        print(render_dataset_contract(rule))
    elif args.format == "datasets-json":
        print(render_dataset_contract_json(rule))
    elif args.format == "plan":
        print(render_rule_plan(rule))
    elif args.format == "text":
        print(render_rule_text(rule))
    else:
        print(render_rule_mermaid(rule))


if __name__ == "__main__":
    main()
