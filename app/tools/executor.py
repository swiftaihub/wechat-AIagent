import logging
from typing import Any

from app.tools.registry import TOOLS

logger = logging.getLogger(__name__)


def execute_tool_call(call: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {
            "ok": False,
            "tool": "none",
            "error": "invalid_tool_call_payload",
            "matched_items": [],
            "reasons": [],
        }

    tool_name = str(call.get("tool", "none") or "none").strip()
    arguments = call.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}

    if tool_name == "none":
        return {
            "ok": True,
            "tool": "none",
            "matched_items": [],
            "reasons": [],
        }

    handler = TOOLS.get(tool_name)
    if handler is None:
        return {
            "ok": False,
            "tool": tool_name,
            "error": "unknown_tool",
            "matched_items": [],
            "reasons": [],
        }

    try:
        if tool_name == "match_advice_from_table":
            query = str(arguments.get("query") or context.get("user_text") or "").strip()
            result = handler(query=query, context=context)
        elif tool_name == "assess_constitution_and_recommend_herbs":
            query = str(arguments.get("query") or context.get("user_text") or "").strip()
            profile = arguments.get("profile", {})
            if not isinstance(profile, dict):
                profile = {}
            result = handler(query=query, profile=profile, context=context)
        else:
            result = handler(**arguments)
    except Exception as exc:
        logger.warning("Tool execution failed for %s: %s", tool_name, exc)
        return {
            "ok": False,
            "tool": tool_name,
            "error": f"tool_execution_failed:{type(exc).__name__}",
            "matched_items": [],
            "reasons": [],
        }

    if not isinstance(result, dict):
        return {
            "ok": False,
            "tool": tool_name,
            "error": "tool_result_not_mapping",
            "matched_items": [],
            "reasons": [],
        }

    result.setdefault("ok", True)
    result.setdefault("tool", tool_name)
    return result
