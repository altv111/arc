from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CorrectionClient(Protocol):
    client_id: str
    client_version: str

    def submit_intents(self, intents: tuple[dict[str, Any], ...], *, mode: str) -> dict[str, Any]:
        ...


class ShadowCorrectionClient:
    """Deterministic correction client that records receipts without external mutation."""

    client_id = "shadow"
    client_version = "1.0.0"

    def submit_intents(self, intents: tuple[dict[str, Any], ...], *, mode: str) -> dict[str, Any]:
        payload = {
            "client": type(self).__name__,
            "client_id": self.client_id,
            "client_version": self.client_version,
            "mode": mode,
            "mutation_performed": False,
            "n_intents": len(intents),
            "intents": list(intents),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return {
            **payload,
            "receipt_id": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        }
