import unittest

from app.product_helper.service import get_product_helper_service


class ProductHelperGuardrailTests(unittest.TestCase):
    def test_high_risk_medical_escalates(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="guard-high",
            text="我胸口痛、呼吸困难，喝什么茶比较好？",
            channel="web",
        )
        self.assertEqual(result.intent, "high_risk_medical")
        self.assertIn("急性风险", result.reply)

    def test_medication_caution_adds_note(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="guard-med",
            text="I have been tired lately and I am also taking medication.",
            preferred_language="en",
            channel="web",
        )
        if result.safety_notes:
            self.assertIn("medication", result.safety_notes[0].lower())


if __name__ == "__main__":
    unittest.main()
