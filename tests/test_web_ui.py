import asyncio
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app, webui_base_path
from app.tools.constitution_advice import reload_constitution_advice_configs
from app.web_ui import (
    _build_html_page,
    _build_intake_payload_from_state,
    _load_intake_config_from_path,
    _load_localized_runtime_text,
)


class WebUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ui_page_loads(self) -> None:
        resp = self.client.get(webui_base_path)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("const CONFIG", resp.text)
        self.assertIn("const INTAKE_CONFIG", resp.text)
        self.assertIn("Menu", resp.text)
        self.assertIn("web_ui.css", resp.text)
        self.assertIn("web_ui.js", resp.text)

    def test_html_page_includes_runtime_welcome_message(self) -> None:
        html = _build_html_page(
            title="Demo Title",
            welcome_message="Custom welcome message",
            api_base_url="/ui/api/chat",
        )
        self.assertIn("Custom welcome message", html)
        self.assertIn("/ui/api/chat", html)
        self.assertIn("window.__WEBUI_BOOT__", html)

    def test_ui_assets_are_served(self) -> None:
        css_response = self.client.get(f"{webui_base_path}/assets/web_ui.css")
        js_response = self.client.get(f"{webui_base_path}/assets/web_ui.js")

        self.assertEqual(css_response.status_code, 200)
        self.assertEqual(js_response.status_code, 200)
        self.assertIn("font-display", css_response.text)
        self.assertIn("window.__WEBUI_BOOT__", js_response.text)

    def test_ui_chat_success(self) -> None:
        with patch("app.web_ui.generate_reply", new=AsyncMock(return_value="ok")):
            resp = self.client.post(
                f"{webui_base_path}/api/chat",
                json={"message": "hello", "user_id": "test-user"},
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["reply"], "ok")
        self.assertFalse(data["timed_out"])

    def test_ui_chat_passes_language_preference(self) -> None:
        mocked_generate = AsyncMock(return_value="ok")
        with patch("app.web_ui.generate_reply", new=mocked_generate):
            resp = self.client.post(
                f"{webui_base_path}/api/chat",
                json={"message": "hello", "user_id": "test-user", "language": "en"},
            )

        self.assertEqual(resp.status_code, 200)
        mocked_generate.assert_awaited_once_with(
            user_id="test-user",
            text="hello",
            preferred_language="en",
        )

    def test_ui_chat_timeout(self) -> None:
        async def _timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("app.web_ui.generate_reply", new=AsyncMock(side_effect=_timeout)):
            resp = self.client.post(
                f"{webui_base_path}/api/chat",
                json={"message": "hello", "user_id": "test-user"},
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["timed_out"])

    def test_intake_config_parses_recent_discomfort_choice_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            advice_path = os.path.join(tmpdir, "herbal_advice.private.yaml")
            intake_path = os.path.join(tmpdir, "questionaire.private.yaml")

            Path(advice_path).write_text(
                textwrap.dedent(
                    """
                    safety_disclaimer: "for test"
                    constitution_recommendations:
                      - id: case_a
                        constitution: c1
                        title: "Case A"
                        symptoms: [易疲劳, 乏力]
                        herbs: [herb_a]
                        usage: "daily"
                      - id: case_b
                        constitution: c2
                        title: "Case B"
                        symptoms: [手脚冰凉, 畏寒]
                        herbs: [herb_b]
                        usage: "daily"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            Path(intake_path).write_text(
                textwrap.dedent(
                    """
                    constitution_scoring_intake:
                      enabled: true
                      fields:
                        - name: recent_discomfort_choice
                          type: single
                          label:
                            zh: 最近不适（单选）
                            en: Recent discomfort (single choice)
                          options_from:
                            source: herbal_advice_symptoms
                            pick: first
                          option_labels:
                            易疲劳:
                              en: Fatigue
                            手脚冰凉:
                              en: Cold hands and feet
                        - name: recent_discomfort_text
                          type: text
                          label:
                            zh: 其它不适症状
                            en: Other discomfort symptoms
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"HERBAL_ADVICE_PATH": advice_path}, clear=False):
                reload_constitution_advice_configs()
                intake_config = _load_intake_config_from_path(intake_path)

            field_names = [field["name"] for field in intake_config["fields"]]
            self.assertEqual(field_names, ["recent_discomfort_choice", "recent_discomfort_text"])
            self.assertEqual(
                [option["value"] for option in intake_config["fields"][0]["options"]],
                ["易疲劳", "手脚冰凉"],
            )
            self.assertEqual(
                intake_config["fields"][0]["options"][0]["label"]["en"],
                "Fatigue",
            )
            self.assertEqual(
                intake_config["fields"][0]["options"][0]["label"]["zh"],
                "易疲劳",
            )
            reload_constitution_advice_configs()

    def test_intake_html_includes_bilingual_recent_discomfort_labels(self) -> None:
        intake_config = {
            "enabled": True,
            "fields": [
                {
                    "name": "recent_discomfort_choice",
                    "type": "single",
                    "label": {"zh": "最近不适（单选）", "en": "Recent discomfort (single choice)"},
                    "options": [
                        {
                            "value": "易疲劳",
                            "label": {"zh": "易疲劳", "en": "Fatigue"},
                        }
                    ],
                },
                {
                    "name": "recent_discomfort_text",
                    "type": "text",
                    "label": {"zh": "其它不适症状", "en": "Other discomfort symptoms"},
                },
            ],
        }

        html = _build_html_page(
            title="Demo Title",
            welcome_message="Custom welcome message",
            api_base_url="/ui/api/chat",
            intake_config=intake_config,
        )

        self.assertIn("最近不适（单选）", html)
        self.assertIn("Recent discomfort (single choice)", html)
        self.assertIn("Fatigue", html)
        self.assertIn("Other discomfort symptoms", html)
        self.assertIn("web_ui.css", html)
        self.assertIn("web_ui.js", html)
        self.assertIn("中文 / English", html)
        self.assertIn("window.__WEBUI_BOOT__ = { CONFIG, INTAKE_CONFIG }", html)

    def test_intake_dynamic_options_use_localized_labels_from_herbal_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            advice_path = Path(tmpdir) / "herbal_advice.private.yaml"
            intake_path = Path(tmpdir) / "questionaire.private.yaml"

            advice_path.write_text(
                textwrap.dedent(
                    """
                    safety_disclaimer: "for test"
                    constitution_recommendations:
                      - id: case_a
                        constitution: c1
                        title: "Case A"
                        symptoms:
                          - zh: 易疲劳
                            en: Fatigue
                          - zh: 乏力
                            en: Low energy
                        herbs: [herb_a]
                        usage: "daily"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            intake_path.write_text(
                textwrap.dedent(
                    """
                    constitution_scoring_intake:
                      enabled: true
                      fields:
                        - name: recent_discomfort_choice
                          type: single
                          label:
                            zh: 最近不适（单选）
                            en: Recent discomfort (single choice)
                          options_from:
                            source: herbal_advice_symptoms
                            pick: first
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"HERBAL_ADVICE_PATH": str(advice_path)}, clear=False):
                reload_constitution_advice_configs()
                intake_config = _load_intake_config_from_path(intake_path)

            option = intake_config["fields"][0]["options"][0]
            self.assertEqual(option["value"], "易疲劳")
            self.assertEqual(option["label"]["zh"], "易疲劳")
            self.assertEqual(option["label"]["en"], "Fatigue")
            reload_constitution_advice_configs()

    def test_localized_runtime_text_supports_bilingual_env_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WEBUI_TITLE": "电子华佗",
                "WEBUI_TITLE_EN": "E-Huatuo",
            },
            clear=False,
        ):
            localized = _load_localized_runtime_text(
                "WEBUI_TITLE",
                default_zh="健康咨询助手",
                default_en="Health Guidance Assistant",
            )

        self.assertEqual(localized["zh"], "电子华佗")
        self.assertEqual(localized["en"], "E-Huatuo")

    def test_intake_config_gracefully_falls_back_to_text_when_dynamic_options_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "questionaire.private.yaml"
            intake_path.write_text(
                textwrap.dedent(
                    """
                    constitution_scoring_intake:
                      enabled: true
                      fields:
                        - name: recent_discomfort_choice
                          type: single
                          label:
                            zh: 最近不适（单选）
                            en: Recent discomfort (single choice)
                          options_from:
                            source: herbal_advice_symptoms
                        - name: recent_discomfort_text
                          type: text
                          label:
                            zh: 其它不适症状
                            en: Other discomfort symptoms
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            missing_advice_path = str(Path(tmpdir) / "missing.private.yaml")
            with patch.dict(os.environ, {"HERBAL_ADVICE_PATH": missing_advice_path}, clear=False):
                reload_constitution_advice_configs()
                intake_config = _load_intake_config_from_path(intake_path)

            self.assertEqual(
                [field["name"] for field in intake_config["fields"]],
                ["recent_discomfort_text"],
            )
            reload_constitution_advice_configs()

    def test_intake_payload_includes_recent_discomfort_choice_and_text(self) -> None:
        fields = [
            {"name": "recent_discomfort_choice", "type": "single"},
            {"name": "recent_discomfort_text", "type": "text"},
        ]
        payload = _build_intake_payload_from_state(
            {
                "recent_discomfort_choice": "易疲劳",
                "recent_discomfort_text": "下午更明显",
            },
            fields,
        )

        self.assertEqual(payload["recent_discomfort_choice"], "易疲劳")
        self.assertEqual(payload["recent_discomfort_text"], "下午更明显")
        self.assertEqual(payload["recent_discomfort"], "易疲劳\n下午更明显")

    def test_intake_config_supports_legacy_recent_discomfort_text_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "questionaire.private.yaml"
            intake_path.write_text(
                textwrap.dedent(
                    """
                    constitution_scoring_intake:
                      enabled: true
                      fields:
                        - name: recent_discomfort
                          type: text
                          label:
                            zh: 近期不适
                            en: Recent discomfort
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            intake_config = _load_intake_config_from_path(intake_path)
            self.assertEqual(len(intake_config["fields"]), 1)
            self.assertEqual(intake_config["fields"][0]["name"], "recent_discomfort")

    def test_intake_payload_supports_legacy_recent_discomfort_text_only(self) -> None:
        fields = [{"name": "recent_discomfort", "type": "text"}]
        payload = _build_intake_payload_from_state(
            {"recent_discomfort": "偶尔胃胀"},
            fields,
        )

        self.assertEqual(payload["recent_discomfort"], "偶尔胃胀")
        self.assertNotIn("recent_discomfort_choice", payload)
        self.assertNotIn("recent_discomfort_text", payload)


if __name__ == "__main__":
    unittest.main()
