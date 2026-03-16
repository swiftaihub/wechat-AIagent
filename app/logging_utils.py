from __future__ import annotations

import hashlib


def hash_identifier(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "anonymous"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
