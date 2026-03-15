from __future__ import annotations

import re
from typing import Any

from app.i18n import normalize_localized_text, normalize_language, resolve_localized_text
from app.product_helper.config import ConstitutionConfig
from app.product_helper.models import ConstitutionAssessment, ConstitutionCandidate


def _apply_signal_score(
    scores: dict[str, float],
    evidences: dict[str, list[str]],
    *,
    constitution_scores: dict[str, Any],
    evidence_label: str,
) -> None:
    for constitution, score in constitution_scores.items():
        if constitution not in scores:
            continue
        try:
            scores[constitution] += float(score)
        except (TypeError, ValueError):
            continue
        evidences[constitution].append(evidence_label)


def _confidence_label(score: float, thresholds: dict[str, Any]) -> str:
    high = float(thresholds.get("high", 8))
    medium = float(thresholds.get("medium", 5))
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


def assess_constitutions(
    *,
    query_text: str,
    intake: dict[str, Any],
    config: ConstitutionConfig,
    language: str,
) -> ConstitutionAssessment:
    lang = normalize_language(language)
    scores = {constitution: 0.0 for constitution in config.constitutions.keys()}
    evidences: dict[str, list[str]] = {constitution: [] for constitution in config.constitutions.keys()}
    signal_summary: list[str] = []
    normalized_query = str(query_text or "").strip().lower()

    for field_name, field_signals in config.signals.items():
        value = intake.get(field_name)
        if value in (None, "", []):
            continue

        matched_values = value if isinstance(value, list) else [value]
        signal_map = field_signals if isinstance(field_signals, dict) else {}
        for item in matched_values:
            option_key = str(item).strip()
            option_cfg = signal_map.get(option_key)
            if not isinstance(option_cfg, dict):
                continue
            label = option_cfg.get("evidence", option_key)
            evidence_text = resolve_localized_text(label, lang, fallback=option_key)
            _apply_signal_score(
                scores,
                evidences,
                constitution_scores=option_cfg.get("scores", {}),
                evidence_label=evidence_text,
            )
            signal_summary.append(evidence_text)

    for signal in config.free_text_signals:
        patterns = tuple(str(item).strip() for item in signal.get("patterns", []) if str(item).strip())
        if not patterns:
            continue
        if any(re.search(pattern, normalized_query, re.IGNORECASE) for pattern in patterns):
            evidence = normalize_localized_text(signal.get("evidence", "text signal"))
            evidence_text = resolve_localized_text(evidence, lang, fallback="text signal")
            _apply_signal_score(
                scores,
                evidences,
                constitution_scores=signal.get("scores", {}),
                evidence_label=evidence_text,
            )
            signal_summary.append(evidence_text)

    candidates: list[ConstitutionCandidate] = []
    top_k = int(config.output_policy.get("top_k", 3) or 3)
    minimum_score = float(config.output_policy.get("minimum_score", 2.5) or 2.5)
    for constitution, total in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        if total < minimum_score:
            continue
        constitution_cfg = config.constitutions.get(constitution, {})
        label = normalize_localized_text(constitution_cfg.get("label", constitution), fallback=constitution)
        description = normalize_localized_text(constitution_cfg.get("description", constitution), fallback=constitution)
        thresholds = constitution_cfg.get("confidence_thresholds", {}) if isinstance(constitution_cfg.get("confidence_thresholds"), dict) else {}
        candidates.append(
            ConstitutionCandidate(
                constitution=constitution,
                label=label,
                score=round(total, 2),
                confidence=_confidence_label(total, thresholds),
                evidence=tuple(dict.fromkeys(evidences.get(constitution, [])))[:4],
                description=description,
            )
        )
        if len(candidates) >= top_k:
            break

    if candidates:
        lead = candidates[0]
        summary = {
            "zh": f"更偏向 {lead.label['zh']}",
            "en": f"Leans more toward {lead.label['en']}",
        }
        confidence = lead.confidence
    else:
        summary = {
            "zh": "目前只看到一些方向性信号，暂不适合说得太满。",
            "en": "I can see a few directional signals, but not enough to sound overly certain.",
        }
        confidence = "low"

    return ConstitutionAssessment(
        candidates=tuple(candidates),
        summary=summary,
        signal_summary=tuple(dict.fromkeys(signal_summary))[:6],
        confidence=confidence,
        conservative_note={
            "zh": "体质判断只作为日常选茶参考，更适合说“更偏向”或“可能更接近”，不等同于诊断。",
            "en": "Constitution guidance here is only for everyday tea selection, so it is better framed as a tendency rather than a diagnosis.",
        },
    )
