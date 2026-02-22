from dataclasses import dataclass
from typing import Any, Callable

from app.tools.advice_table import match_advice_from_table
from app.tools.constitution_advice import assess_constitution_and_recommend_herbs


ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    json_schema: dict[str, Any]


TOOL_SPECS: dict[str, ToolSpec] = {
    "assess_constitution_and_recommend_herbs": ToolSpec(
        name="assess_constitution_and_recommend_herbs",
        description=(
            "Assess constitution tendency with a scoring matrix and return herbal wellness guidance, "
            "follow-up questions, and required handoff appendix."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "User message for constitution assessment."},
                "profile": {
                    "type": "object",
                    "description": "Optional structured profile fields such as age/gender/sleep/diet.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "match_advice_from_table": ToolSpec(
        name="match_advice_from_table",
        description=(
            "Match user intent against a local advice table and return structured advice, "
            "handoffs, follow-up questions, and safety notes."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "User query text to match in table."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
}


TOOLS: dict[str, ToolHandler] = {
    "assess_constitution_and_recommend_herbs": assess_constitution_and_recommend_herbs,
    "match_advice_from_table": match_advice_from_table,
}


def get_tool_specs_for_prompt() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "json_schema": spec.json_schema,
        }
        for spec in TOOL_SPECS.values()
    ]
