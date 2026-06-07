from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arc.core.context import ScopeKey


class NodeStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"
    ERROR = "error"


class IndeterminateError(Exception):
    """Raised when data prevents a reliable pass/fail verdict."""

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    media_type: str = "application/json"
    size_bytes: int = Field(default=0, ge=0)
    schema_version: str = "v1"


class InputSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    datasets: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ResolvedInputs(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    data: dict[str, Any] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_keys_match(self) -> "ResolvedInputs":
        if set(self.data.keys()) != set(self.versions.keys()):
            missing = set(self.data) ^ set(self.versions)
            raise ValueError(f"data and versions must have matching keys; differ on: {sorted(missing)}")
        return self


class HandlerOutput(BaseModel):
    """Pure-compute output from a CheckHandler."""

    model_config = ConfigDict(extra="forbid")

    status: NodeStatus
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    breached_scopes: list[ScopeKey] = Field(default_factory=list)
    evidence_payloads: dict[str, Any] = Field(default_factory=dict)
    downstream_hints: dict[str, Any] = Field(default_factory=dict)


class NodeResult(BaseModel):
    """Auditable result of a single node execution."""

    model_config = ConfigDict(extra="forbid")

    rule_id: int
    node_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    status: NodeStatus
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    breached_scopes: list[ScopeKey] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    downstream_hints: dict[str, Any] = Field(default_factory=dict)

    handler_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)

    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)

    upstream_data_versions: dict[str, str] = Field(default_factory=dict)

    @field_validator("started_at", "finished_at")
    @classmethod
    def _require_tzaware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _check_temporal_order(self) -> "NodeResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be >= started_at")
        return self
