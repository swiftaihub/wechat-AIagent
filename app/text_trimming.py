from __future__ import annotations

import re


_URL_PATTERNS = (
    r"\[[^\]]+\]\(https?://[^\s)]+\)",
    r"https?://\S+",
)
_SENTENCE_BOUNDARIES = (".", "!", "?", "。", "！", "？", "\n")
_COMPLETE_ENDINGS = (".", "!", "?", "。", "！", "？", "...", "…", ")", "]", "}", '"', "'", "”")


def looks_cut_mid_sentence(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if normalized.endswith(("...", "…")):
        base = normalized.rstrip(".…").rstrip()
        return bool(base) and not base.endswith(_COMPLETE_ENDINGS)
    return not normalized.endswith(_COMPLETE_ENDINGS)


def smart_trim_to_limit(
    text: str,
    *,
    max_chars: int,
    trim_suffix: str,
) -> str:
    normalized = str(text or "").strip()
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    suffix = str(trim_suffix or "").strip()
    trim_at = max(0, max_chars - len(suffix))
    for pattern in _URL_PATTERNS:
        for match in re.finditer(pattern, normalized):
            if match.start() < trim_at < match.end():
                trim_at = match.start()
                break

    window_start = max(0, trim_at - 260)
    boundary_positions = [normalized.rfind(boundary, window_start, trim_at) for boundary in _SENTENCE_BOUNDARIES]
    best_boundary = max(boundary_positions)
    if best_boundary >= window_start + 80:
        candidate = normalized[: best_boundary + 1].rstrip()
        if candidate:
            return f"{candidate}{suffix}" if suffix else candidate

    candidate = normalized[:trim_at].rstrip() or normalized[:max_chars].rstrip()
    return f"{candidate}{suffix}" if suffix else candidate
