from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import arc.handlers  # noqa: F401 - import-time registration
from arc.clients.reporting import CSVReportingClient
from arc.core.context import BARunContext
from arc.core.evidence_store import EvidenceStore
from arc.core.run_state import RunStateStore
from arc.rule import build_rule_from_json
from arc.run_summary import render_run_summary
from arc.runner import Runner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an ARC BA rule against a fixture/reporting root.")
    parser.add_argument("--rule", default="fixtures/rules/row1.json", help="Path to rule JSON.")
    parser.add_argument(
        "--fixtures",
        default="fixtures/ECR/2026-06-04",
        help="Fixture/reporting root containing CSV datasets.",
    )
    parser.add_argument("--ba", default="ECR", help="Business area.")
    parser.add_argument("--business-date", default="2026-06-04", help="Business date in YYYY-MM-DD.")
    parser.add_argument("--run-type", choices=("1dvar", "10dvar", "10dsvar"), default="1dvar")
    parser.add_argument("--snapshot-id", default="fixture-2026-06-04", help="SNAP/reporting snapshot id.")
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id.")
    parser.add_argument("--artifacts", default=".arc_runs", help="Directory for run/evidence/state artifacts.")
    parser.add_argument("--summary", action="store_true", help="Print a human-readable run summary.")
    args = parser.parse_args()

    root = Path.cwd()
    fixtures = _path(root, args.fixtures)
    artifacts = _path(root, args.artifacts)

    evidence_store = EvidenceStore(artifacts / "evidence")
    run_state_store = RunStateStore(artifacts / "state")

    rule = build_rule_from_json(
        _path(root, args.rule),
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )

    ctx = BARunContext(
        ba=args.ba,
        business_date=date.fromisoformat(args.business_date),
        run_type=args.run_type,
        snapshot_id=args.snapshot_id,
        config_version="local-dev",
        code_version="local-dev",
    )

    runner = Runner(
        reporting_client=CSVReportingClient(fixtures),
        evidence_store=evidence_store,
        run_state_store=run_state_store,
        runs_root=artifacts / "runs",
    )

    report = runner.run_rule(rule, ctx, run_id=args.run_id)
    print(report.to_dict())
    if args.summary:
        print()
        print(render_run_summary(report))


def _path(root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else root / path


if __name__ == "__main__":
    main()
