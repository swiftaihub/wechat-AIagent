import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.tools.constitution_advice import (
    assess_constitution_and_recommend_herbs,
    extract_recent_discomfort_option_values,
    load_herbal_advice_config,
    reload_constitution_advice_configs,
)


class ConstitutionAdviceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_scoring_path = os.environ.get("CONSTITUTION_SCORING_PATH")
        self._old_advice_path = os.environ.get("HERBAL_ADVICE_PATH")
        self._old_scoring_example = os.environ.get("CONSTITUTION_SCORING_EXAMPLE_PATH")
        self._old_advice_example = os.environ.get("HERBAL_ADVICE_EXAMPLE_PATH")

    def tearDown(self) -> None:
        if self._old_scoring_path is None:
            os.environ.pop("CONSTITUTION_SCORING_PATH", None)
        else:
            os.environ["CONSTITUTION_SCORING_PATH"] = self._old_scoring_path

        if self._old_advice_path is None:
            os.environ.pop("HERBAL_ADVICE_PATH", None)
        else:
            os.environ["HERBAL_ADVICE_PATH"] = self._old_advice_path

        if self._old_scoring_example is None:
            os.environ.pop("CONSTITUTION_SCORING_EXAMPLE_PATH", None)
        else:
            os.environ["CONSTITUTION_SCORING_EXAMPLE_PATH"] = self._old_scoring_example

        if self._old_advice_example is None:
            os.environ.pop("HERBAL_ADVICE_EXAMPLE_PATH", None)
        else:
            os.environ["HERBAL_ADVICE_EXAMPLE_PATH"] = self._old_advice_example

        reload_constitution_advice_configs()

    def test_constitution_scoring_and_herbal_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            scoring_path = Path(tmpdir) / "constitution_scoring.private.yaml"
            advice_path = Path(tmpdir) / "herbal_advice.private.yaml"

            scoring_path.write_text(
                textwrap.dedent(
                    """
                    schema:
                      fields: [age, gender, sleep, diet, bowel, emotion, exercise, recent_discomfort]
                      constitutions: [气虚, 阳虚, 阴虚, 痰湿, 湿热, 气滞, 血瘀, 气血两虚]
                    rules:
                      age_bucket:
                        "18-35":
                          add: {阴虚: 1}
                      gender:
                        "女":
                          add: {气血两虚: 1}
                      sleep:
                        options:
                          "多梦易醒/睡浅":
                            add: {阴虚: 2, 气滞: 1}
                            match_keywords: [多梦, 易醒, 睡浅]
                      diet:
                        options:
                          "口干爱喝水/偏辛辣且越吃越上火":
                            add: {阴虚: 2, 湿热: 1}
                            match_keywords: [口干, 上火]
                      bowel:
                        options:
                          "便秘干结/羊屎/排便费劲":
                            add: {阴虚: 2}
                            match_keywords: [便秘, 干结]
                      emotion:
                        options:
                          "压力大且睡差":
                            add: {阴虚: 1, 气滞: 2}
                            match_keywords: [压力大, 睡差]
                      exercise:
                        options:
                          "运动后口干心烦/睡更差":
                            add: {阴虚: 2}
                            match_keywords: [运动后口干, 睡更差]
                    output_policy:
                      top_k: 2
                      min_gap_for_single: 3
                      min_score_to_output: 3
                      tie_breaker_priority: [阴虚, 气滞, 湿热, 气虚, 阳虚, 痰湿, 血瘀, 气血两虚]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            advice_path.write_text(
                textwrap.dedent(
                    """
                    safety_disclaimer: "仅供参考"
                    required_append_text: |
                      微信：laiguo0516
                      公司地址：新疆乌鲁木齐市 水磨沟区 新民路街道 药材巷30号
                    company_handoffs:
                      - type: address
                        label: 公司地址
                        address: 新疆乌鲁木齐市 水磨沟区 新民路街道 药材巷30号
                    constitution_recommendations:
                      - id: yin_xu_case
                        constitution: 阴虚
                        title: 阴虚调养建议
                        symptoms: [口干, 睡浅, 便秘]
                        herbs: [西洋参片, 麦冬, 枸杞]
                        usage: "西洋参2g、麦冬6g、枸杞8g，连用7天。"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            os.environ["CONSTITUTION_SCORING_PATH"] = str(scoring_path)
            os.environ["HERBAL_ADVICE_PATH"] = str(advice_path)
            reload_constitution_advice_configs()

            query = (
                "年龄: 29\\n性别: 女\\n睡眠: 多梦易醒, 睡浅\\n饮食: 口干爱喝水\\n"
                "排便: 便秘干结\\n情绪: 压力大睡差\\n运动: 运动后口干心烦\\n最近不适: 上火"
            )
            result = assess_constitution_and_recommend_herbs(query=query, profile={}, context={})

            self.assertTrue(result["ok"])
            self.assertEqual(result["tool"], "assess_constitution_and_recommend_herbs")
            self.assertTrue(result["constitution_assessment"]["selected"])
            self.assertEqual(result["constitution_assessment"]["selected"][0]["constitution"], "阴虚")
            self.assertTrue(result["herbal_recommendations"])
            self.assertTrue(result["requires_company_append"])
            self.assertIn("微信：laiguo0516", result["required_append_text"])

            matched_item = result["matched_items"][0]
            self.assertEqual(matched_item["handoffs"][0]["address"], "新疆乌鲁木齐市 水磨沟区 新民路街道 药材巷30号")

    def test_title_parenthetical_match_bypasses_constitution_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            scoring_path = Path(tmpdir) / "constitution_scoring.private.yaml"
            advice_path = Path(tmpdir) / "herbal_advice.private.yaml"

            scoring_path.write_text(
                textwrap.dedent(
                    """
                    schema:
                      fields: [age, gender, sleep, diet, bowel, emotion, exercise, recent_discomfort]
                      constitutions: [c1, c2]
                    rules:
                      age_bucket:
                        "18-35":
                          add: {c1: 1}
                    output_policy:
                      top_k: 2
                      min_gap_for_single: 3
                      min_score_to_output: 9
                      tie_breaker_priority: [c1, c2]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            advice_path.write_text(
                textwrap.dedent(
                    """
                    safety_disclaimer: "for test"
                    constitution_recommendations:
                      - id: acne_case
                        constitution: c1
                        title: "Skin wellness recommendation (acne flare)"
                        symptoms: [pimples, oily]
                        herbs: [herb_a, herb_b]
                        usage: "daily"
                      - id: constipation_case
                        constitution: c2
                        title: "Digestive wellness recommendation (dry constipation)"
                        symptoms: [constipation]
                        herbs: [herb_c]
                        usage: "night"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            os.environ["CONSTITUTION_SCORING_PATH"] = str(scoring_path)
            os.environ["HERBAL_ADVICE_PATH"] = str(advice_path)
            reload_constitution_advice_configs()

            result = assess_constitution_and_recommend_herbs(
                query="I have pimples recently with oily skin.",
                profile={},
                context={},
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["direct_symptom_match"])
            self.assertTrue(result["constitution_assessment"]["bypassed"])
            self.assertEqual(result["constitution_assessment"]["selected"], [])
            self.assertEqual(result["herbal_recommendations"][0]["id"], "acne_case")
            self.assertEqual(result["followup_questions"], [])
            self.assertEqual(result["reasons"][0]["kind"], "direct_symptom_match")

    def test_extract_recent_discomfort_option_values_uses_first_symptom_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            advice_path = Path(tmpdir) / "herbal_advice.private.yaml"
            advice_path.write_text(
                textwrap.dedent(
                    """
                    safety_disclaimer: "for test"
                    constitution_recommendations:
                      - id: case_a
                        constitution: c1
                        title: "Case A"
                        symptoms: [fatigue, restless sleep, dry mouth]
                        herbs: [herb_a]
                        usage: "daily"
                      - id: case_b
                        constitution: c2
                        title: "Case B"
                        symptoms: [cold hands, loose stool]
                        herbs: [herb_b]
                        usage: "daily"
                      - id: case_c
                        constitution: c3
                        title: "Case C"
                        symptoms: [fatigue, headache]
                        herbs: [herb_c]
                        usage: "daily"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            os.environ["HERBAL_ADVICE_PATH"] = str(advice_path)
            reload_constitution_advice_configs()

            values = extract_recent_discomfort_option_values(load_herbal_advice_config())
            self.assertEqual(values, ("fatigue", "cold hands"))

    def test_recent_discomfort_profile_fields_merge_choice_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            scoring_path = Path(tmpdir) / "constitution_scoring.private.yaml"
            advice_path = Path(tmpdir) / "herbal_advice.private.yaml"

            scoring_path.write_text(
                textwrap.dedent(
                    """
                    schema:
                      fields: [age, gender, sleep, diet, bowel, emotion, exercise, recent_discomfort]
                      constitutions: [c1]
                    rules: {}
                    output_policy:
                      top_k: 1
                      min_gap_for_single: 1
                      min_score_to_output: 9
                      tie_breaker_priority: [c1]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            advice_path.write_text(
                textwrap.dedent(
                    """
                    safety_disclaimer: "for test"
                    constitution_recommendations:
                      - id: case_a
                        constitution: c1
                        title: "Case A"
                        symptoms: [fatigue]
                        herbs: [herb_a]
                        usage: "daily"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            os.environ["CONSTITUTION_SCORING_PATH"] = str(scoring_path)
            os.environ["HERBAL_ADVICE_PATH"] = str(advice_path)
            reload_constitution_advice_configs()

            result = assess_constitution_and_recommend_herbs(
                query="Please help.",
                profile={
                    "recent_discomfort_choice": "fatigue",
                    "recent_discomfort_text": "worse in the afternoon",
                },
                context={},
            )

            self.assertEqual(
                result["input_profile"]["recent_discomfort"],
                "fatigue\nworse in the afternoon",
            )


if __name__ == "__main__":
    unittest.main()
