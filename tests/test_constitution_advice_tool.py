import unittest

from app.tools.constitution_advice import (
    assess_constitution_and_recommend_herbs,
    extract_recent_discomfort_option_values,
)


class ConstitutionAdviceToolTests(unittest.TestCase):
    def test_fatigue_query_returns_qi_direction_and_products(self) -> None:
        result = assess_constitution_and_recommend_herbs(
            query="最近很累，说话都懒，恢复也慢，有没有适合我的茶？",
            profile={},
            context={"channel": "web", "user_id": "fatigue-user"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"], "symptom_or_discomfort_guidance")
        self.assertTrue(result["product_recommendations"])
        self.assertIn("气虚", [item["constitution"] for item in result["constitution_assessment"]["selected"]])
        self.assertLessEqual(len(result["product_recommendations"]), 2)

    def test_dryness_query_returns_yin_support_direction(self) -> None:
        result = assess_constitution_and_recommend_herbs(
            query="I've been staying up late and feeling dry lately. What would fit me best?",
            profile={},
            context={"channel": "web", "user_id": "dry-user", "preferred_language": "en"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["intent"], "symptom_or_discomfort_guidance")
        self.assertTrue(result["product_recommendations"])
        constitutions = [item["label"]["en"] for item in result["constitution_assessment"]["selected"]]
        self.assertIn("Yin deficiency", constitutions)

    def test_recent_discomfort_options_come_from_questionnaire(self) -> None:
        values = extract_recent_discomfort_option_values()
        self.assertIn("fatigue", values)
        self.assertIn("dryness_after_late_nights", values)


if __name__ == "__main__":
    unittest.main()
