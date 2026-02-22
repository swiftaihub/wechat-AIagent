import os
import unittest

from app.llm_core import parse_tool_call_json
from app.tools.executor import execute_tool_call


class ToolCallParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_threshold = os.environ.get("TOOL_CALL_CONFIDENCE_THRESHOLD")
        os.environ["TOOL_CALL_CONFIDENCE_THRESHOLD"] = "0.55"

    def tearDown(self) -> None:
        if self._old_threshold is None:
            os.environ.pop("TOOL_CALL_CONFIDENCE_THRESHOLD", None)
        else:
            os.environ["TOOL_CALL_CONFIDENCE_THRESHOLD"] = self._old_threshold

    def test_valid_json_is_parsed(self) -> None:
        parsed = parse_tool_call_json(
            '{"tool":"match_advice_from_table","arguments":{"query":"veneers"},"confidence":0.92,"reason":"match"}'
        )

        self.assertEqual(parsed["tool"], "match_advice_from_table")
        self.assertEqual(parsed["arguments"]["query"], "veneers")
        self.assertAlmostEqual(parsed["confidence"], 0.92)
        self.assertNotIn("parse_error", parsed)

    def test_invalid_json_falls_back_to_none(self) -> None:
        parsed = parse_tool_call_json("not-a-json")

        self.assertEqual(parsed["tool"], "none")
        self.assertEqual(parsed["arguments"], {})
        self.assertIn("parse_error", parsed)

    def test_low_confidence_forces_none(self) -> None:
        parsed = parse_tool_call_json(
            '{"tool":"match_advice_from_table","arguments":{"query":"veneers"},"confidence":0.4,"reason":"weak"}'
        )

        self.assertEqual(parsed["tool"], "none")
        self.assertEqual(parsed["arguments"], {})
        self.assertTrue(parsed.get("forced_none", False))

    def test_unknown_tool_rejected_by_executor(self) -> None:
        parsed = parse_tool_call_json(
            '{"tool":"not_registered_tool","arguments":{"query":"x"},"confidence":0.9,"reason":"x"}'
        )
        result = execute_tool_call(parsed, context={"user_text": "hello"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
