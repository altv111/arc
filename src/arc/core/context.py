from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RunType = Literal["1dvar", "10dvar", "10dsvar"]


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _short_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:16]


class ScopeKey(BaseModel):
    """Generic, deterministic business scope identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    levels: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @field_validator("levels", mode="before")
    @classmethod
    def _normalise_levels(cls, value: Any) -> dict[str, tuple[str, ...]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("ScopeKey.levels must be a mapping")

        out: dict[str, tuple[str, ...]] = {}
        for key in sorted(value):
            vals = value[key]
            if not isinstance(key, str) or not key:
                raise ValueError(f"ScopeKey level name must be non-empty string, got {key!r}")
            if isinstance(vals, str):
                raise ValueError(
                    f"ScopeKey level {key!r}: values must be a sequence of strings, not a string"
                )
            if vals is None:
                continue

            seen: set[str] = set()
            normalised: list[str] = []
            for item in vals:
                if not isinstance(item, str):
                    raise ValueError(
                        f"ScopeKey level {key!r}: entries must be strings, got {type(item).__name__}"
                    )
                if item not in seen:
                    seen.add(item)
                    normalised.append(item)
            normalised.sort()
            if normalised:
                out[key] = tuple(normalised)
        return out

    def is_empty(self) -> bool:
        return not self.levels

    def canonical_hash(self) -> str:
        return _short_hash(self.model_dump(mode="json"))

    def __hash__(self) -> int:
        return hash(self.canonical_hash())


class BARunContext(BaseModel):
    """Identifiers that scope a single Business Area run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ba: str = Field(min_length=1)
    business_date: date
    run_type: RunType
    snapshot_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1)

    def determinism_key(self) -> str:
        return _short_hash(
            {
                "ba": self.ba,
                "business_date": self.business_date.isoformat(),
                "run_type": self.run_type,
                "snapshot_id": self.snapshot_id,
                "config_version": self.config_version,
                "code_version": self.code_version,
            }
        )
