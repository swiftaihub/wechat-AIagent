import unittest

from app.llm_core import parse_tool_call_json
from app.tools.executor import execute_tool_call


class ToolCallParsingTests(unittest.TestCase):
    def test_valid_json_is_parsed(self) -> None:
        parsed = parse_tool_call_json(
            '{"tool":"assess_constitution_and_recommend_products","arguments":{"query":"fatigue"},"confidence":0.92,"reason":"product_help"}'
        )

        self.assertEqual(parsed["tool"], "assess_constitution_and_recommend_products")
        self.assertEqual(parsed["arguments"]["query"], "fatigue")
        self.assertAlmostEqual(parsed["confidence"], 0.92)

    def test_invalid_json_falls_back_to_none(self) -> None:
        parsed = parse_tool_call_json("not-a-json")
        self.assertEqual(parsed["tool"], "none")
        self.assertIn("parse_error", parsed)

    def test_low_confidence_forces_none(self) -> None:
        parsed = parse_tool_call_json(
            '{"tool":"assess_constitution_and_recommend_products","arguments":{"query":"fatigue"},"confidence":0.2,"reason":"weak"}'
        )

        self.assertEqual(parsed["tool"], "none")
        self.assertTrue(parsed.get("forced_none", False))

    def test_executor_rejects_unknown_tool(self) -> None:
        result = execute_tool_call({"tool": "missing_tool", "arguments": {"query": "x"}}, {"user_text": "hello"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
