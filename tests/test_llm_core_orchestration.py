import os
import unittest
from unittest.mock import AsyncMock, patch

from app.llm_core import generate_reply, generate_reply_result
from app.product_helper.models import HelperResult
from app.runtime_config import reset_runtime_config_cache
from app.usage_guard import reset_usage_guard_cache


class _StubService:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    def handle(self, **kwargs):
        self.calls.append(kwargs)
        return HelperResult(
            language="en",
            intent="product_detail",
            mode="product_detail",
            reply=self.reply,
            needs_followup=False,
            followup_questions=(),
            intake_state={},
            constitution_assessment=None,
            product_recommendations=(),
            support_links=(),
            safety_notes=(),
            metadata={"allow_naturalization": False},
        )


class LlmCoreOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        reset_runtime_config_cache()
        reset_usage_guard_cache()

    async def test_generate_reply_passes_wechat_channel_and_trims_output(self) -> None:
        stub = _StubService(reply="A" * 600)

        with patch("app.llm_core.get_product_helper_service", return_value=stub):
            reply = await generate_reply(user_id="wx-channel", text="hello", preferred_language="en", channel="wechat")

        self.assertEqual(stub.calls[0]["channel"], "wechat")
        self.assertLessEqual(len(reply), 320)

    async def test_naturalization_falls_back_when_output_breaks_compliance(self) -> None:
        with (
            patch("app.llm_core._naturalization_enabled", return_value=True),
            patch("app.llm_core.llm_chat", new=AsyncMock(return_value="This tea will cure your fatigue.")),
        ):
            reply = await generate_reply(
                user_id="naturalize-fallback",
                text="I've been tired lately and want a tea recommendation.",
                preferred_language="en",
                channel="web",
            )

        self.assertNotIn("cure", reply.lower())
        self.assertTrue(any(name in reply for name in ("Red Date Dawn Vitality Tea", "Amber Silk Restore Tea", "Citrus Cloud Harmony Tea")))

    async def test_force_naturalization_calls_llm_for_normal_reply(self) -> None:
        stub = _StubService(reply="Deterministic draft reply.")

        with patch.dict(os.environ, {"OPENCLAW_FORCE_NATURALIZATION": "1"}, clear=False):
            reset_runtime_config_cache()
            reset_usage_guard_cache()
            with (
                patch("app.llm_core.get_product_helper_service", return_value=stub),
                patch("app.llm_core.llm_chat", new=AsyncMock(return_value="Naturalized final reply.")),
            ):
                reply = await generate_reply(
                    user_id="force-naturalize",
                    text="Tell me more about this tea.",
                    preferred_language="en",
                    channel="web",
                )

        self.assertEqual(reply, "Naturalized final reply.")

    async def test_generate_reply_result_returns_structured_block_for_oversized_input(self) -> None:
        long_text = "A" * 5000

        outcome = await generate_reply_result(
            user_id="oversized-user",
            text=long_text,
            preferred_language="en",
            channel="web",
        )

        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.error_code, "INPUT_TOO_LONG")
        self.assertIn("too long", outcome.reply.lower())

    async def test_rate_limit_blocks_before_service_handle_runs(self) -> None:
        env = {
            "RATE_LIMIT_MAX_REQUESTS": "1",
            "MAX_REQUESTS_PER_HOUR": "50",
            "MAX_REQUESTS_PER_DAY": "200",
            "MAX_MESSAGES_PER_USER_SESSION": "20",
            "USAGE_LIMIT_MESSAGE_EN": "Usage limit reached. Please try again later.",
        }
        stub = _StubService(reply="ok")

        with patch.dict(os.environ, env, clear=False):
            reset_runtime_config_cache()
            reset_usage_guard_cache()
            with patch("app.llm_core.get_product_helper_service", return_value=stub):
                first = await generate_reply_result(user_id="rate-core", text="hello", preferred_language="en", channel="web")
                second = await generate_reply_result(user_id="rate-core", text="again", preferred_language="en", channel="web")

        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.error_code, "RATE_LIMITED")
        self.assertEqual(second.reply, "Usage limit reached. Please try again later.")
        self.assertEqual(len(stub.calls), 1)


if __name__ == "__main__":
    unittest.main()
