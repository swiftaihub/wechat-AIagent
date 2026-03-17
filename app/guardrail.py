import re
from dataclasses import dataclass

from app.prompt_runtime import GuardrailSettings
from app.text_trimming import smart_trim_to_limit


@dataclass(frozen=True)
class InputGuardrailResult:
    blocked: bool
    text: str


class GuardrailEngine:
    def __init__(self, settings: GuardrailSettings) -> None:
        self._settings = settings
        self._blocked_input_patterns = self._compile_patterns(
            settings.blocked_input_patterns,
            "blocked_input_patterns",
        )
        self._blocked_output_patterns = self._compile_patterns(
            settings.blocked_output_patterns,
            "blocked_output_patterns",
        )
        self._redaction_patterns = self._compile_patterns(
            settings.redaction_patterns,
            "redaction_patterns",
        )

    def check_input(self, user_text: str) -> InputGuardrailResult:
        text = (user_text or "").strip()
        if not self._settings.enabled:
            return InputGuardrailResult(blocked=False, text=text)

        for pattern in self._blocked_input_patterns:
            if pattern.search(text):
                return InputGuardrailResult(
                    blocked=True,
                    text=self._settings.blocked_response,
                )

        return InputGuardrailResult(blocked=False, text=text)

    def sanitize_output(self, model_output: str) -> str:
        text = (model_output or "").strip()
        if not text:
            return self._settings.fallback_response

        if not self._settings.enabled:
            return text

        for pattern in self._blocked_output_patterns:
            if pattern.search(text):
                return self._settings.blocked_response

        for pattern in self._redaction_patterns:
            text = pattern.sub(self._settings.redaction_replacement, text)

        text = text.strip()
        if not text:
            return self._settings.fallback_response

        if self._settings.max_output_chars > 0 and len(text) > self._settings.max_output_chars:
            text = smart_trim_to_limit(
                text,
                max_chars=self._settings.max_output_chars,
                trim_suffix=self._settings.trim_suffix,
            )

        return text or self._settings.fallback_response

    @staticmethod
    def _compile_patterns(patterns: tuple[str, ...], field_name: str) -> tuple[re.Pattern[str], ...]:
        compiled: list[re.Pattern[str]] = []
        for raw_pattern in patterns:
            try:
                compiled.append(re.compile(raw_pattern))
            except re.error as exc:
                raise ValueError(f"Invalid regex in guardrail.{field_name}: {raw_pattern}") from exc
        return tuple(compiled)
