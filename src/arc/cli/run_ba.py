from __future__ import annotations

from datetime import date
from pathlib import Path

import arc.handlers  # noqa: F401 - import-time registration
from arc.clients.reporting import CSVReportingClient
from arc.core.context import BARunContext
from arc.core.evidence_store import EvidenceStore
from arc.core.run_state import RunStateStore
from arc.rule import build_rule_from_json
from arc.runner import Runner


def main() -> None:
    root = Path.cwd()
    fixtures = root / "fixtures" / "ECR" / "2026-06-04"
    artifacts = root / ".arc_runs"

    evidence_store = EvidenceStore(artifacts / "evidence")
    run_state_store = RunStateStore(artifacts / "state")

    rule = build_rule_from_json(
        root / "fixtures" / "rules" / "row1.json",
        evidence_store=evidence_store,
        run_state_store=run_state_store,
    )

    ctx = BARunContext(
        ba="ECR",
        business_date=date(2026, 6, 4),
        run_type="1dvar",
        snapshot_id="fixture-2026-06-04",
        config_version="local-dev",
        code_version="local-dev",
    )

    runner = Runner(
        reporting_client=CSVReportingClient(fixtures),
        evidence_store=evidence_store,
        run_state_store=run_state_store,
        runs_root=artifacts / "runs",
    )

    report = runner.run_rule(rule, ctx)
    print(report.to_dict())


if __name__ == "__main__":
    main()
