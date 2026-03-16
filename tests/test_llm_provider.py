import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.llm_provider import LLMProviderConfigError, llm_chat, reset_llm_provider_state
from app.runtime_config import reset_runtime_config_cache


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.request = httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=httpx.Response(self.status_code, request=self.request))


class DashScopeProviderTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        reset_runtime_config_cache()
        reset_llm_provider_state()

    async def test_llm_chat_uses_dashscope_openai_compatible_endpoint(self) -> None:
        env = {
            "DASHSCOPE_API_KEY": "test-key",
            "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "DASHSCOPE_MODEL": "qwen-flash",
            "LLM_MAX_RETRIES": "0",
        }
        fake_response = _FakeResponse(
            status_code=200,
            payload={"choices": [{"message": {"content": "Hello from DashScope"}}]},
        )

        with patch.dict(os.environ, env, clear=False):
            reset_runtime_config_cache()
            with patch("app.llm_provider.httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mock_post:
                reply = await llm_chat(system_prompt="sys", user_prompt="user", user_id="provider-user")

        self.assertEqual(reply, "Hello from DashScope")
        self.assertEqual(mock_post.await_args.args[0], "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(mock_post.await_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(mock_post.await_args.kwargs["json"]["model"], "qwen-flash")
        self.assertEqual(mock_post.await_args.kwargs["json"]["messages"][0]["role"], "system")

    async def test_llm_chat_raises_when_api_key_missing(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}, clear=False):
            reset_runtime_config_cache()
            with self.assertRaises(LLMProviderConfigError):
                await llm_chat(system_prompt="sys", user_prompt="user")


if __name__ == "__main__":
    unittest.main()
