from typing import Any

from app.tools.registry import TOOLS


def execute_tool_call(call: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {"ok": False, "tool": "none", "error": "invalid_tool_call_payload", "matched_items": [], "reasons": []}

    tool_name = str(call.get("tool", "none") or "none").strip()
    arguments = call.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}

    if tool_name == "none":
        return {"ok": True, "tool": "none", "matched_items": [], "reasons": []}

    handler = TOOLS.get(tool_name)
    if handler is None:
        return {"ok": False, "tool": tool_name, "error": "unknown_tool", "matched_items": [], "reasons": []}

    query = str(arguments.get("query") or context.get("user_text") or "").strip()
    profile = arguments.get("profile", {})
    if not isinstance(profile, dict):
        profile = {}
    enriched_context = dict(context or {})
    if profile:
        enriched_context["structured_profile"] = profile
    try:
        return handler(query=query, profile=profile, context=enriched_context)
    except Exception as exc:
        return {
            "ok": False,
            "tool": tool_name,
            "error": "tool_execution_failed",
            "detail": str(exc),
            "matched_items": [],
            "reasons": [],
        }
