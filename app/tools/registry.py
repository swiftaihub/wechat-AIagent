from dataclasses import dataclass
from typing import Any, Callable

from app.tools.constitution_advice import assess_constitution_and_recommend_herbs


ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    json_schema: dict[str, Any]


TOOL_SPECS: dict[str, ToolSpec] = {
    "assess_constitution_and_recommend_products": ToolSpec(
        name="assess_constitution_and_recommend_products",
        description=(
            "Infer likely constitution tendency, rank 1-3 tea products, and select the most relevant supporting links "
            "for brand-safe product guidance."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "User message or intake payload."},
                "profile": {"type": "object", "description": "Optional structured intake fields."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "assess_constitution_and_recommend_herbs": ToolSpec(
        name="assess_constitution_and_recommend_herbs",
        description="Backward-compatible alias for product-helper recommendations.",
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "profile": {"type": "object"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
}


TOOLS: dict[str, ToolHandler] = {
    "assess_constitution_and_recommend_products": assess_constitution_and_recommend_herbs,
    "assess_constitution_and_recommend_herbs": assess_constitution_and_recommend_herbs,
}


def get_tool_specs_for_prompt() -> list[dict[str, Any]]:
    return [
        {"name": spec.name, "description": spec.description, "json_schema": spec.json_schema}
        for spec in TOOL_SPECS.values()
    ]
