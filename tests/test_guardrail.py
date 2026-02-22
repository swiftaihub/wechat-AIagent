import unittest

from app.guardrail import GuardrailEngine
from app.prompt_runtime import GuardrailSettings


class GuardrailEngineTests(unittest.TestCase):
    def test_blocked_input_returns_blocked_response(self) -> None:
        settings = GuardrailSettings(
            blocked_input_patterns=(r"(?i)forbidden",),
            blocked_response="BLOCKED",
        )
        engine = GuardrailEngine(settings)

        result = engine.check_input("This contains forbidden content")

        self.assertTrue(result.blocked)
        self.assertEqual(result.text, "BLOCKED")

    def test_output_redaction_and_trim(self) -> None:
        settings = GuardrailSettings(
            max_output_chars=20,
            redaction_patterns=(r"SECRET_[A-Z0-9]+",),
            redaction_replacement="[MASKED]",
            trim_suffix="...",
        )
        engine = GuardrailEngine(settings)

        sanitized = engine.sanitize_output("Value SECRET_123456789 is present")

        self.assertNotIn("SECRET_", sanitized)
        self.assertLessEqual(len(sanitized), 20)

    def test_blocked_output_pattern(self) -> None:
        settings = GuardrailSettings(
            blocked_output_patterns=(r"(?i)do_not_return",),
            blocked_response="BLOCKED_OUT",
        )
        engine = GuardrailEngine(settings)

        sanitized = engine.sanitize_output("please DO_NOT_RETURN this")

        self.assertEqual(sanitized, "BLOCKED_OUT")

    def test_prescription_pattern_allows_negated_disclaimer_and_basic_dosage(self) -> None:
        settings = GuardrailSettings(
            blocked_output_patterns=(
                r"(?i)((开具|开出|(?<!不)给出|(?<!不)提供|制定|执行|按方).{0,8}(处方|药方|方剂)|处方如下|药方如下|方剂如下|抓药方案|按方抓药|联合用药方案|配伍方案)",
            ),
            blocked_response="BLOCKED_OUT",
        )
        engine = GuardrailEngine(settings)

        sanitized = engine.sanitize_output("不做疾病诊断，不给出处方。可参考西洋参2g、麦冬6g。")

        self.assertNotEqual(sanitized, "BLOCKED_OUT")
        self.assertIn("西洋参2g", sanitized)

    def test_prescription_pattern_blocks_explicit_prescription_instruction(self) -> None:
        settings = GuardrailSettings(
            blocked_output_patterns=(
                r"(?i)((开具|开出|(?<!不)给出|(?<!不)提供|制定|执行|按方).{0,8}(处方|药方|方剂)|处方如下|药方如下|方剂如下|抓药方案|按方抓药|联合用药方案|配伍方案)",
            ),
            blocked_response="BLOCKED_OUT",
        )
        engine = GuardrailEngine(settings)

        sanitized = engine.sanitize_output("建议按方抓药，以下为药方如下：A药10g，B药8g。")

        self.assertEqual(sanitized, "BLOCKED_OUT")


if __name__ == "__main__":
    unittest.main()
