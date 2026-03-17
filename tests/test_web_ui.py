import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.llm_core import ReplyOutcome
from app.main import app, webui_base_path
from app.web_ui import WEB_UI_ASSET_DIR, _build_html_page, _build_intake_payload_from_state, INTAKE_CONFIG


class WebUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ui_page_loads(self) -> None:
        resp = self.client.get(webui_base_path)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("window.__WEBUI_BOOT__", resp.text)
        self.assertIn("web_ui.css", resp.text)
        self.assertIn("web_ui.js", resp.text)
        self.assertIn('id="brandPanel"', resp.text)
        self.assertIn('id="brandPanelBtn"', resp.text)
        self.assertIn("Herbal Tea Recommendation Helper", resp.text)
        self.assertNotIn('data-api-base-label="1"', resp.text)

    def test_html_page_includes_runtime_welcome_message(self) -> None:
        html = _build_html_page(
            title="Demo Title",
            welcome_message="Custom welcome message",
            api_base_url="/custom/api/chat",
            intake_config=INTAKE_CONFIG,
        )
        self.assertIn("Custom welcome message", html)
        self.assertIn("/custom/api/chat", html)

    def test_build_intake_payload_is_generic(self) -> None:
        payload = _build_intake_payload_from_state(
            {
                "use_case": "gifting",
                "recent_discomfort_multi": ["fatigue", "dryness_after_late_nights"],
                "gift_target": "mother",
                "free_text_recent_discomfort": "wants something not too bitter",
            },
            list(INTAKE_CONFIG.get("fields", [])),
        )
        self.assertEqual(payload["use_case"], "gifting")
        self.assertEqual(payload["gift_target"], "mother")
        self.assertNotIn("recent_discomfort_multi", payload)

    def test_intake_config_resolves_product_dropdown_options(self) -> None:
        product_field = next(
            field
            for field in INTAKE_CONFIG.get("fields", [])
            if field.get("name") == "selected_product_slug"
        )
        self.assertEqual(product_field.get("ui_variant"), "dropdown")
        self.assertTrue(product_field.get("options"))

    def test_use_case_field_uses_spotlight_variant(self) -> None:
        use_case_field = next(
            field
            for field in INTAKE_CONFIG.get("fields", [])
            if field.get("name") == "use_case"
        )
        self.assertEqual(use_case_field.get("ui_variant"), "spotlight-grid")
        self.assertTrue(use_case_field.get("options"))
        self.assertTrue(any(option.get("description") for option in use_case_field.get("options", [])))

    def test_ui_chat_success(self) -> None:
        with patch("app.web_ui.generate_reply_result", new=AsyncMock(return_value=ReplyOutcome(ok=True, reply="ok"))):
            resp = self.client.post(f"{webui_base_path}/api/chat", json={"message": "hello", "user_id": "test-user"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["reply"], "ok")

    def test_ui_chat_timeout(self) -> None:
        async def _timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("app.web_ui.generate_reply_result", new=AsyncMock(side_effect=_timeout)):
            resp = self.client.post(f"{webui_base_path}/api/chat", json={"message": "hello", "user_id": "test-user"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["timed_out"])

    def test_ui_chat_returns_block_metadata(self) -> None:
        blocked = ReplyOutcome(
            ok=False,
            reply="Please slow down a little.",
            blocked=True,
            error_code="RATE_LIMITED",
            retry_after_seconds=120,
            unblock_at="2026-03-16T12:00:00+00:00",
        )

        with patch("app.web_ui.generate_reply_result", new=AsyncMock(return_value=blocked)):
            resp = self.client.post(f"{webui_base_path}/api/chat", json={"message": "hello", "user_id": "test-user"})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertTrue(resp.json()["blocked"])
        self.assertEqual(resp.json()["error_code"], "RATE_LIMITED")

    def test_web_ui_js_supports_safe_markdown_link_rendering(self) -> None:
        js = (WEB_UI_ASSET_DIR / "web_ui.js").read_text(encoding="utf-8")
        self.assertIn("renderMessageHtml", js)
        self.assertIn('target="_blank"', js)
        self.assertIn("noopener noreferrer", js)


if __name__ == "__main__":
    unittest.main()
