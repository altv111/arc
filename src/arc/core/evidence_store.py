from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from arc.core.results import EvidenceRef


class EvidenceStore:
    """Content-addressable on-disk JSON evidence store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, name: str, payload: Any) -> EvidenceRef:
        raw = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
        content_hash = hashlib.sha256(raw).hexdigest()
        path = self.root / f"{content_hash}.json"

        if path.exists() and path.read_bytes() != raw:
            raise ValueError(f"content hash collision for {content_hash}")

        path.write_bytes(raw)
        return EvidenceRef(
            name=name,
            content_hash=content_hash,
            uri=str(path),
            size_bytes=len(raw),
        )
