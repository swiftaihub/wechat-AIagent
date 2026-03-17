import os
import unittest
from unittest.mock import AsyncMock, patch

from app.llm_core import generate_reply, generate_reply_result
from app.memory_store import reset_memory_store_cache
from app.product_helper.content import load_catalog_bundle
from app.product_helper.models import HelperResult, LinkEntry, ProductRecommendation
from app.runtime_config import reset_runtime_config_cache
from app.usage_guard import reset_usage_guard_cache


class _StubService:
    def __init__(
        self,
        reply: str,
        *,
        intent: str = "product_detail",
        support_links: tuple[LinkEntry, ...] = (),
        product_recommendations: tuple[ProductRecommendation, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.reply = reply
        self.intent = intent
        self.support_links = support_links
        self.product_recommendations = product_recommendations
        self.metadata = metadata or {}
        self.calls: list[dict[str, object]] = []

    def handle(self, **kwargs):
        self.calls.append(kwargs)
        metadata = {"allow_naturalization": False}
        metadata.update(self.metadata)
        return HelperResult(
            language="en",
            intent=self.intent,
            mode=self.intent,
            reply=self.reply,
            needs_followup=False,
            followup_questions=(),
            intake_state={},
            constitution_assessment=None,
            product_recommendations=self.product_recommendations,
            support_links=self.support_links,
            safety_notes=(),
            metadata=metadata,
        )


class _RepeatAwareService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def handle(self, **kwargs):
        self.calls.append(kwargs)
        reply = "Same draft reply." if not kwargs.get("loop_detected") else "Fresh follow-up reply."
        return HelperResult(
            language="en",
            intent="product_detail",
            mode="product_detail",
            reply=reply,
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
        reset_memory_store_cache()

    async def test_generate_reply_passes_wechat_channel_and_trims_output(self) -> None:
        stub = _StubService(reply="A" * 600)

        with patch("app.llm_core.get_product_helper_service", return_value=stub):
            reply = await generate_reply(user_id="wx-channel", text="hello", preferred_language="en", channel="wechat")

        self.assertEqual(stub.calls[0]["channel"], "wechat")
        self.assertLessEqual(len(reply), 420)

    async def test_naturalization_falls_back_when_output_breaks_compliance(self) -> None:
        with (
            patch("app.llm_core._naturalization_enabled", return_value=True),
            patch("app.llm_core.llm_chat", new=AsyncMock(return_value="This tea will cure your fatigue.")),
        ):
            reply = await generate_reply(
                user_id="naturalize-fallback",
                text="I've been tired lately, with low energy and slow recovery. What tea would fit me best?",
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

    async def test_repeat_detection_retries_with_active_history_window(self) -> None:
        stub = _RepeatAwareService()
        reset_runtime_config_cache()
        reset_usage_guard_cache()
        reset_memory_store_cache()

        with (
            patch("app.llm_core.get_product_helper_service", return_value=stub),
            patch("app.llm_core._naturalization_requested", return_value=False),
        ):
            first = await generate_reply_result(
                user_id="repeat-history",
                text="I feel tired",
                preferred_language="en",
                channel="web",
            )
            second = await generate_reply_result(
                user_id="repeat-history",
                text="what should I drink?",
                preferred_language="en",
                channel="web",
            )

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(second.reply, "Fresh follow-up reply.")
        self.assertEqual(len(stub.calls), 3)
        second_attempt = stub.calls[1]
        self.assertIn("history_messages", second_attempt)
        self.assertEqual(second_attempt["history_messages"][-1].role, "user")
        self.assertEqual(second_attempt["history_messages"][-1].content, "what should I drink?")
        self.assertFalse(bool(second_attempt.get("loop_detected")))
        self.assertTrue(bool(stub.calls[2].get("loop_detected")))

    async def test_force_naturalization_skips_grounded_product_reply(self) -> None:
        bundle = load_catalog_bundle()
        product = bundle.products[0]
        recommendation = ProductRecommendation(
            product=product,
            score=8.5,
            why=("Grounded test",),
            taste="balanced",
            when_to_drink="daily",
            caution="",
        )
        link = LinkEntry(
            id=f"product:{product.slug}",
            type="product",
            slug=product.slug,
            zh_title=product.name["zh"],
            en_title=product.name["en"],
            url=product.buy_link,
            tags=(),
            related_constitutions=(),
            related_discomforts=(),
            related_ingredients=product.ingredients,
            related_products=(product.slug,),
            use_cases=(),
            funnel_stage="purchase",
        )
        draft_reply = f"{product.name['en']} is a grounded option.\n\n- [View Product]({product.buy_link})"
        stub = _StubService(
            reply=draft_reply,
            support_links=(link,),
            product_recommendations=(recommendation,),
            metadata={"allow_naturalization": True, "grounding_required": True},
        )

        with patch.dict(os.environ, {"OPENCLAW_FORCE_NATURALIZATION": "1"}, clear=False):
            reset_runtime_config_cache()
            reset_usage_guard_cache()
            mocked_llm = AsyncMock(return_value="Made-up Nebula Tea [Buy](https://bad.example.com)")
            with (
                patch("app.llm_core.get_product_helper_service", return_value=stub),
                patch("app.llm_core.llm_chat", new=mocked_llm),
            ):
                reply = await generate_reply(
                    user_id="grounded-naturalize",
                    text="Recommend a tea for me.",
                    preferred_language="en",
                    channel="web",
                )

        self.assertEqual(reply, draft_reply)
        self.assertIn("/en/products/", reply)
        mocked_llm.assert_not_awaited()

    async def test_naturalized_reply_with_unknown_url_falls_back_to_draft(self) -> None:
        stub = _StubService(
            reply="Safe draft reply.",
            intent="general_brand_scope_qna",
            metadata={"allow_naturalization": True, "grounding_required": False},
        )

        with (
            patch("app.llm_core._naturalization_enabled", return_value=True),
            patch("app.llm_core.get_product_helper_service", return_value=stub),
            patch("app.llm_core.llm_chat", new=AsyncMock(return_value="Click here: [Bad Link](https://bad.example.com)")),
        ):
            reply = await generate_reply(
                user_id="invalid-url-fallback",
                text="hello",
                preferred_language="en",
                channel="web",
            )

        self.assertEqual(reply, "Safe draft reply.")


if __name__ == "__main__":
    unittest.main()
