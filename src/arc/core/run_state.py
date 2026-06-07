from __future__ import annotations

from pathlib import Path

from arc.core.results import NodeResult


class RunStateStore:
    """Idempotency cache keyed by deterministic node inputs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> NodeResult | None:
        path = self._path(key)
        if not path.exists():
            return None
        return NodeResult.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, key: str, result: NodeResult) -> None:
        self._path(key).write_text(result.model_dump_json(indent=2), encoding="utf-8")
