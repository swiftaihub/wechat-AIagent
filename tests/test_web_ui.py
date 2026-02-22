import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app, webui_base_path
from app.web_ui import _build_html_page


class WebUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ui_page_loads(self) -> None:
        resp = self.client.get(webui_base_path)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("const CONFIG", resp.text)
        self.assertIn("Menu", resp.text)

    def test_html_page_includes_runtime_welcome_message(self) -> None:
        html = _build_html_page(
            title="Demo Title",
            welcome_message="Custom welcome message",
            api_base_url="/ui/api/chat",
        )
        self.assertIn("Custom welcome message", html)
        self.assertIn("/ui/api/chat", html)

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


if __name__ == "__main__":
    unittest.main()
