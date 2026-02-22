import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


class WebUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ui_page_loads(self) -> None:
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("OpenClaw Local UI", resp.text)

    def test_ui_chat_success(self) -> None:
        with patch("app.web_ui.generate_reply", new=AsyncMock(return_value="ok")):
            resp = self.client.post(
                "/ui/api/chat",
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
                "/ui/api/chat",
                json={"message": "hello", "user_id": "test-user"},
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["timed_out"])


if __name__ == "__main__":
    unittest.main()
