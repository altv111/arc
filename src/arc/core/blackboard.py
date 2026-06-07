from __future__ import annotations

from arc.core.context import ScopeKey
from arc.core.results import NodeResult


class Blackboard:
    """In-memory per-run shared state."""

    def __init__(self) -> None:
        self._data: dict[tuple[int, str, str], NodeResult] = {}

    def put(self, result: NodeResult, scope: ScopeKey, *, overwrite: bool = False) -> None:
        key = (result.rule_id, result.check_id, scope.canonical_hash())
        if not overwrite and key in self._data:
            raise KeyError(f"blackboard key already exists: {key}")
        self._data[key] = result

    def get(self, rule_id: int, check_id: str, scope: ScopeKey) -> NodeResult | None:
        return self._data.get((rule_id, check_id, scope.canonical_hash()))
