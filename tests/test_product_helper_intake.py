import unittest

from app.product_helper.config import load_questionnaire_config
from app.product_helper.intake import build_visible_intake_payload, next_followup_question, normalize_intake, visible_intake_fields


class ProductHelperIntakeTests(unittest.TestCase):
    def test_visible_fields_for_gifting_keep_only_relevant_sections(self) -> None:
        questionnaire = load_questionnaire_config()
        state = {"use_case": "gifting", "recent_discomfort_multi": ["fatigue"], "gift_target": "mother"}
        field_names = [field["name"] for field in visible_intake_fields(questionnaire.fields, state)]
        self.assertIn("use_case", field_names)
        self.assertIn("gift_target", field_names)
        self.assertIn("gift_budget_tier", field_names)
        self.assertNotIn("recent_discomfort_multi", field_names)

    def test_normalize_intake_prunes_hidden_fields_after_use_case_switch(self) -> None:
        questionnaire = load_questionnaire_config()
        normalized = normalize_intake(
            {
                "use_case": "gifting",
                "recent_discomfort_multi": ["fatigue"],
                "gift_target": "mother",
                "premium_preference": "premium_forward",
            },
            questionnaire,
        )
        self.assertEqual(normalized["use_case"], "gifting")
        self.assertIn("gift_target", normalized)
        self.assertNotIn("recent_discomfort_multi", normalized)

    def test_build_visible_payload_only_keeps_active_use_case_fields(self) -> None:
        questionnaire = load_questionnaire_config()
        payload = build_visible_intake_payload(
            {
                "use_case": "recent_discomfort_guidance",
                "recent_discomfort_multi": ["fatigue"],
                "gift_target": "mother",
                "free_text_recent_discomfort": "recovering slowly",
            },
            questionnaire.fields,
        )
        self.assertIn("recent_discomfort_multi", payload)
        self.assertIn("recent_discomfort_combined", payload)
        self.assertNotIn("gift_target", payload)

    def test_followup_question_respects_current_use_case(self) -> None:
        questionnaire = load_questionnaire_config()
        prompt = next_followup_question(
            questionnaire=questionnaire,
            intake={"use_case": "gifting"},
            intent="gifting_recommendation",
            language="zh",
        )
        self.assertTrue("妈妈" in prompt or "伴侣" in prompt or "预算" in prompt or "高级" in prompt)
        self.assertNotIn("最近更明显的状态", prompt)


if __name__ == "__main__":
    unittest.main()
