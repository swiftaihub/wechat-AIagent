import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from app.i18n import normalize_localized_text, normalize_language, resolve_localized_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptProfile:
    system_prompt: Mapping[str, str]
    user_prompt_template: Mapping[str, str]


@dataclass(frozen=True)
class GuardrailSettings:
    enabled: bool = True
    max_output_chars: int = 2200
    blocked_input_patterns: tuple[str, ...] = ()
    blocked_output_patterns: tuple[str, ...] = ()
    redaction_patterns: tuple[str, ...] = ()
    redaction_replacement: str | Mapping[str, str] = "[REDACTED]"
    blocked_response: str | Mapping[str, str] = "This request cannot be processed."
    fallback_response: str | Mapping[str, str] = "I cannot generate a valid reply right now."
    trim_suffix: str | Mapping[str, str] = "..."


@dataclass(frozen=True)
class PromptSettings:
    source_path: Path
    default_profile: str
    profiles: Mapping[str, PromptProfile]
    guardrail: GuardrailSettings


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


class PromptRuntime:
    def __init__(self, settings: PromptSettings) -> None:
        self._settings = settings

    @property
    def source_path(self) -> Path:
        return self._settings.source_path

    @property
    def default_profile(self) -> str:
        return self._settings.default_profile

    @property
    def guardrail_settings(self) -> GuardrailSettings:
        return self.guardrail_settings_for_language("zh")

    def guardrail_settings_for_language(self, language: str | None = None) -> GuardrailSettings:
        settings = self._settings.guardrail
        lang = normalize_language(language)
        return GuardrailSettings(
            enabled=settings.enabled,
            max_output_chars=settings.max_output_chars,
            blocked_input_patterns=settings.blocked_input_patterns,
            blocked_output_patterns=settings.blocked_output_patterns,
            redaction_patterns=settings.redaction_patterns,
            redaction_replacement=resolve_localized_text(
                settings.redaction_replacement,
                lang,
                fallback="[REDACTED]",
            ),
            blocked_response=resolve_localized_text(
                settings.blocked_response,
                lang,
                fallback="This request cannot be processed.",
            ),
            fallback_response=resolve_localized_text(
                settings.fallback_response,
                lang,
                fallback="I cannot generate a valid reply right now.",
            ),
            trim_suffix=resolve_localized_text(
                settings.trim_suffix,
                lang,
                fallback="...",
            ),
        )

    def system_prompt(self, profile: str | None = None, *, language: str | None = None) -> str:
        active_profile = self._profile(profile)
        return resolve_localized_text(active_profile.system_prompt, language)

    def render_user_prompt(
        self,
        *,
        user_text: str,
        profile: str | None = None,
        user_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        extra_variables: Mapping[str, Any] | None = None,
        language: str | None = None,
    ) -> str:
        active_profile = self._profile(profile)
        context = context or {}

        context_lines = []
        for key, value in context.items():
            context_lines.append(f"{key}: {value}")
        context_block = "\n".join(context_lines).strip() or "-"

        render_payload = _SafeFormatDict(
            {
                "user_text": (user_text or "").strip(),
                "user_id": user_id or "",
                "context_block": context_block,
            }
        )

        for key, value in context.items():
            render_payload[str(key)] = "" if value is None else str(value)

        if extra_variables:
            for key, value in extra_variables.items():
                render_payload[str(key)] = "" if value is None else str(value)

        template = resolve_localized_text(active_profile.user_prompt_template, language)
        return template.format_map(render_payload).strip()

    def _profile(self, profile: str | None) -> PromptProfile:
        profile_name = (profile or self._settings.default_profile).strip()
        if profile_name not in self._settings.profiles:
            available = ", ".join(sorted(self._settings.profiles.keys()))
            raise KeyError(f"Unknown prompt profile '{profile_name}'. Available: {available}")
        return self._settings.profiles[profile_name]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_path(path_value: str) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


def _resolve_prompt_config_path() -> Path:
    from_env = os.getenv("PROMPT_CONFIG_PATH", "").strip()
    if from_env:
        env_path = _resolve_path(from_env)
        if not env_path.exists():
            raise FileNotFoundError(f"PROMPT_CONFIG_PATH file not found: {env_path}")
        return env_path

    private_path = _resolve_path("config/prompt.private.yaml")
    if private_path.exists():
        return private_path

    example_path = _resolve_path(os.getenv("PROMPT_EXAMPLE_PATH", "config/prompt.example.yaml"))
    if example_path.exists():
        logger.warning("Using prompt example config: %s", example_path)
        return example_path

    raise FileNotFoundError(
        "No prompt config found. Set PROMPT_CONFIG_PATH or create config/prompt.private.yaml."
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Prompt config root must be a mapping: {path}")
    return raw


def _to_prompt_profile(profile_name: str, raw_profile: Any) -> PromptProfile:
    if not isinstance(raw_profile, dict):
        raise ValueError(f"Profile '{profile_name}' must be a mapping.")

    system_prompt = normalize_localized_text(raw_profile.get("system_prompt", ""))
    user_prompt_template = normalize_localized_text(raw_profile.get("user_prompt_template", ""))

    if not (system_prompt["zh"] or system_prompt["en"]):
        raise ValueError(f"Profile '{profile_name}' missing system_prompt.")
    if not (user_prompt_template["zh"] or user_prompt_template["en"]):
        raise ValueError(f"Profile '{profile_name}' missing user_prompt_template.")

    return PromptProfile(
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
    )


def _to_guardrail_settings(raw_guardrail: Any) -> GuardrailSettings:
    if not isinstance(raw_guardrail, dict):
        raw_guardrail = {}

    def _list_of_strings(key: str) -> tuple[str, ...]:
        values = raw_guardrail.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"guardrail.{key} must be a list.")
        return tuple(str(item).strip() for item in values if str(item).strip())

    max_output_chars = int(raw_guardrail.get("max_output_chars", 2200))
    if max_output_chars < 0:
        raise ValueError("guardrail.max_output_chars must be >= 0.")

    return GuardrailSettings(
        enabled=bool(raw_guardrail.get("enabled", True)),
        max_output_chars=max_output_chars,
        blocked_input_patterns=_list_of_strings("blocked_input_patterns"),
        blocked_output_patterns=_list_of_strings("blocked_output_patterns"),
        redaction_patterns=_list_of_strings("redaction_patterns"),
        redaction_replacement=normalize_localized_text(
            raw_guardrail.get("redaction_replacement", "[REDACTED]"),
            fallback="[REDACTED]",
        ),
        blocked_response=normalize_localized_text(
            raw_guardrail.get("blocked_response", "This request cannot be processed."),
            fallback="This request cannot be processed.",
        ),
        fallback_response=normalize_localized_text(
            raw_guardrail.get("fallback_response", "I cannot generate a valid reply right now."),
            fallback="I cannot generate a valid reply right now.",
        ),
        trim_suffix=normalize_localized_text(
            raw_guardrail.get("trim_suffix", "..."),
            fallback="...",
        ),
    )


def load_prompt_settings() -> PromptSettings:
    source_path = _resolve_prompt_config_path()
    raw = _read_yaml(source_path)

    raw_profiles = raw.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ValueError("Prompt config must include a non-empty 'profiles' mapping.")

    profiles: dict[str, PromptProfile] = {}
    for profile_name, raw_profile in raw_profiles.items():
        name = str(profile_name).strip()
        if not name:
            raise ValueError("Prompt profile name cannot be empty.")
        profiles[name] = _to_prompt_profile(name, raw_profile)

    default_profile = str(raw.get("default_profile", "")).strip() or next(iter(profiles))
    if default_profile not in profiles:
        raise ValueError(f"default_profile '{default_profile}' is not defined in profiles.")

    guardrail = _to_guardrail_settings(raw.get("guardrail", {}))

    return PromptSettings(
        source_path=source_path,
        default_profile=default_profile,
        profiles=profiles,
        guardrail=guardrail,
    )


@lru_cache(maxsize=1)
def get_prompt_runtime() -> PromptRuntime:
    return PromptRuntime(load_prompt_settings())


def reload_prompt_runtime() -> PromptRuntime:
    get_prompt_runtime.cache_clear()
    return get_prompt_runtime()
