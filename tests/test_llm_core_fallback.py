import os
import unittest
from unittest.mock import AsyncMock, patch

from app import llm_core
from app.guardrail import GuardrailEngine
from app.memory_store import reset_memory_store_cache
from app.prompt_runtime import GuardrailSettings


class _FakeRuntime:
    def __init__(self) -> None:
        self.guardrail_settings = GuardrailSettings(enabled=True, max_output_chars=900)

    def system_prompt(self, profile: str | None = None) -> str:
        return f"SYSTEM::{profile or 'default'}"

    def render_user_prompt(
        self,
        *,
        user_text: str,
        profile: str | None = None,
        user_id: str | None = None,
        context=None,
        extra_variables=None,
    ) -> str:
        context = context or {}
        extra_variables = extra_variables or {}
        return (
            f"PROFILE={profile or 'default'}\n"
            f"USER={user_id or ''}\n"
            f"CHANNEL={context.get('channel', '')}\n"
            f"HISTORY={context.get('recent_history', '')}\n"
            f"TEXT={user_text}\n"
            f"TOOLS={extra_variables.get('tools_json', '')}\n"
            f"TOOL_CALL={extra_variables.get('tool_call_json', '')}\n"
            f"TOOL_RESULT={extra_variables.get('tool_result_json', '')}"
        )


class LlmCoreFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._old_tool_enabled = os.environ.get("TOOL_CALLING_ENABLED")
        self._old_threshold = os.environ.get("TOOL_CALL_CONFIDENCE_THRESHOLD")
        self._old_memory_enabled = os.environ.get("OPENCLAW_MEMORY_ENABLED")
        self._old_memory_turns = os.environ.get("OPENCLAW_MEMORY_MAX_TURNS")
        self._old_memory_ttl = os.environ.get("OPENCLAW_MEMORY_TTL_SECONDS")
        os.environ["TOOL_CALLING_ENABLED"] = "1"
        os.environ["TOOL_CALL_CONFIDENCE_THRESHOLD"] = "0.55"
        os.environ["OPENCLAW_MEMORY_ENABLED"] = "1"
        os.environ["OPENCLAW_MEMORY_MAX_TURNS"] = "4"
        os.environ["OPENCLAW_MEMORY_TTL_SECONDS"] = "1800"
        llm_core._get_guardrail_engine.cache_clear()
        reset_memory_store_cache()

    def tearDown(self) -> None:
        if self._old_tool_enabled is None:
            os.environ.pop("TOOL_CALLING_ENABLED", None)
        else:
            os.environ["TOOL_CALLING_ENABLED"] = self._old_tool_enabled

        if self._old_threshold is None:
            os.environ.pop("TOOL_CALL_CONFIDENCE_THRESHOLD", None)
        else:
            os.environ["TOOL_CALL_CONFIDENCE_THRESHOLD"] = self._old_threshold

        if self._old_memory_enabled is None:
            os.environ.pop("OPENCLAW_MEMORY_ENABLED", None)
        else:
            os.environ["OPENCLAW_MEMORY_ENABLED"] = self._old_memory_enabled

        if self._old_memory_turns is None:
            os.environ.pop("OPENCLAW_MEMORY_MAX_TURNS", None)
        else:
            os.environ["OPENCLAW_MEMORY_MAX_TURNS"] = self._old_memory_turns

        if self._old_memory_ttl is None:
            os.environ.pop("OPENCLAW_MEMORY_TTL_SECONDS", None)
        else:
            os.environ["OPENCLAW_MEMORY_TTL_SECONDS"] = self._old_memory_ttl

        llm_core._get_guardrail_engine.cache_clear()
        reset_memory_store_cache()

    def test_enforce_herbal_only_reply_removes_generic_lifestyle_sections(self) -> None:
        raw = (
            "【体质倾向分析】\n"
            "偏阴虚。\n\n"
            "【日常调养建议】\n"
            "1. **调整作息时间**：尽量在23点前入睡。\n"
            "2. **饮食调理**：少油少辣。\n"
            "3. **适量运动**：每天快走30分钟。\n"
            "4. **保持良好情绪状态**：学会放松。\n"
            "可参考中药：西洋参片2g、麦冬6g、枸杞8g。\n\n"
            "【温馨提醒】\n"
            "仅供参考。"
        )

        cleaned = llm_core._enforce_herbal_only_reply(raw)

        self.assertIn("【中药养生建议】", cleaned)
        self.assertNotIn("调整作息时间", cleaned)
        self.assertNotIn("饮食调理", cleaned)
        self.assertNotIn("适量运动", cleaned)
        self.assertNotIn("保持良好情绪状态", cleaned)

    async def test_planner_parse_failure_falls_back_to_direct(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch(
                "app.llm_core.ollama_chat",
                new=AsyncMock(side_effect=["not-json", "DIRECT_REPLY"]),
            ) as mocked_chat,
        ):
            reply = await llm_core.generate_reply(user_id="u1", text="hello")

        self.assertEqual(reply, "DIRECT_REPLY")
        self.assertEqual(mocked_chat.await_count, 2)

    async def test_none_tool_out_of_scope_returns_refusal_without_final_call(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        planner_json = (
            '{"tool":"none","arguments":{},"confidence":0.90,"reason":"non-wellness coding question"}'
        )

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch("app.llm_core.ollama_chat", new=AsyncMock(side_effect=[planner_json])) as mocked_chat,
        ):
            reply = await llm_core.generate_reply(user_id="u_off", text="How to write Python code?")

        self.assertIn("仅提供中医养生与中药调养相关信息", reply)
        self.assertEqual(mocked_chat.await_count, 1)

    async def test_none_tool_meta_chat_uses_final_generation(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        planner_json = '{"tool":"none","arguments":{},"confidence":0.90,"reason":"capability question"}'

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch(
                "app.llm_core.ollama_chat",
                new=AsyncMock(side_effect=[planner_json, "我可以提供中医养生建议。"]),
            ) as mocked_chat,
        ):
            reply = await llm_core.generate_reply(user_id="u_meta", text="你是否拥有专业知识？")

        self.assertIn("中医养生建议", reply)
        self.assertEqual(mocked_chat.await_count, 2)
        second_call_user_prompt = mocked_chat.await_args_list[1].kwargs.get("user_prompt", "")
        self.assertIn('"intent": "meta_chat"', second_call_user_prompt)

    async def test_short_term_memory_is_injected_into_second_turn(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        os.environ["TOOL_CALLING_ENABLED"] = "0"

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch("app.llm_core.ollama_chat", new=AsyncMock(return_value="OK")) as mocked_chat,
        ):
            await llm_core.generate_reply(user_id="mem-u1", text="first question")
            await llm_core.generate_reply(user_id="mem-u1", text="second question")

        self.assertEqual(mocked_chat.await_count, 2)
        second_prompt = mocked_chat.await_args_list[1].kwargs.get("user_prompt", "")
        self.assertIn("HISTORY=[User] first question", second_prompt)

    async def test_tool_failure_falls_back_to_direct(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        planner_json = (
            '{"tool":"match_advice_from_table","arguments":{"query":"veneers"},'
            '"confidence":0.95,"reason":"keyword"}'
        )

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch(
                "app.llm_core.ollama_chat",
                new=AsyncMock(side_effect=[planner_json, "DIRECT_AFTER_TOOL_ERROR"]),
            ) as mocked_chat,
            patch(
                "app.llm_core.execute_tool_call",
                return_value={
                    "ok": False,
                    "tool": "match_advice_from_table",
                    "error": "tool_execution_failed",
                    "matched_items": [],
                    "reasons": [],
                },
            ),
        ):
            reply = await llm_core.generate_reply(user_id="u2", text="need veneer")

        self.assertEqual(reply, "DIRECT_AFTER_TOOL_ERROR")
        self.assertEqual(mocked_chat.await_count, 2)

    async def test_unapproved_handoff_values_are_not_returned(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        planner_json = (
            '{"tool":"match_advice_from_table","arguments":{"query":"veneers"},'
            '"confidence":0.95,"reason":"keyword"}'
        )
        tool_result = {
            "ok": True,
            "tool": "match_advice_from_table",
            "matched_items": [
                {
                    "id": "dental_veneers",
                    "title": "Dental veneer consultation",
                    "advice": "Check oral condition first.",
                    "handoffs": [
                        {
                            "type": "questionnaire",
                            "label": "Form",
                            "url": "https://example.com/forms/veneers",
                        },
                        {
                            "type": "address",
                            "label": "Clinic",
                            "address": "123 Main St, Newark, DE",
                        },
                    ],
                    "followup_questions": [],
                    "safety": {},
                }
            ],
            "reasons": [],
        }

        final_with_hallucination = (
            "Use this link https://evil.example.com, call +1-999-999-0000, "
            "and visit 999 Fake St for booking."
        )

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch(
                "app.llm_core.ollama_chat",
                new=AsyncMock(side_effect=[planner_json, final_with_hallucination]),
            ),
            patch("app.llm_core.execute_tool_call", return_value=tool_result),
        ):
            reply = await llm_core.generate_reply(user_id="u3", text="need veneer support")

        self.assertNotIn("https://evil.example.com", reply)
        self.assertNotIn("+1-999-999-0000", reply)
        self.assertNotIn("999 Fake St", reply)
        self.assertIn("https://example.com/forms/veneers", reply)
        self.assertIn("123 Main St, Newark, DE", reply)

    async def test_tool_calling_disabled_keeps_single_stage_behavior(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        os.environ["TOOL_CALLING_ENABLED"] = "0"

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch("app.llm_core.ollama_chat", new=AsyncMock(return_value="DIRECT_ONLY")) as mocked_chat,
        ):
            reply = await llm_core.generate_reply(user_id="u4", text="hello")

        self.assertEqual(reply, "DIRECT_ONLY")
        self.assertEqual(mocked_chat.await_count, 1)

    async def test_tool_disabled_out_of_scope_returns_refusal_without_model_call(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        os.environ["TOOL_CALLING_ENABLED"] = "0"

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch("app.llm_core.ollama_chat", new=AsyncMock()) as mocked_chat,
        ):
            reply = await llm_core.generate_reply(user_id="u6", text="请帮我写一段Python代码")

        self.assertIn("仅提供中医养生与中药调养相关信息", reply)
        self.assertEqual(mocked_chat.await_count, 0)

    async def test_required_appendix_is_appended_when_missing(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        planner_json = (
            '{"tool":"assess_constitution_and_recommend_herbs","arguments":{"query":"失眠口干"},'
            '"confidence":0.95,"reason":"health request"}'
        )
        tool_result = {
            "ok": True,
            "tool": "assess_constitution_and_recommend_herbs",
            "matched_items": [
                {
                    "id": "yin_xu_case",
                    "title": "阴虚调养建议",
                    "advice": "可参考西洋参片、麦冬、枸杞。",
                    "handoffs": [
                        {
                            "type": "address",
                            "label": "公司地址",
                            "address": "新疆乌鲁木齐市 水磨沟区 新民路街道 药材巷30号",
                        }
                    ],
                    "followup_questions": [],
                    "safety": {},
                }
            ],
            "reasons": [],
            "requires_company_append": True,
            "required_append_text": (
                "微信：laiguo0516\n"
                "公司名称：参茸药业\n"
                "公司地址：新疆乌鲁木齐市 水磨沟区 新民路街道 药材巷30号"
            ),
        }

        final_without_appendix = "【体质倾向分析】\n偏阴虚。\n\n【日常调养建议】\n可参考西洋参片。"

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch(
                "app.llm_core.ollama_chat",
                new=AsyncMock(side_effect=[planner_json, final_without_appendix]),
            ),
            patch("app.llm_core.execute_tool_call", return_value=tool_result),
        ):
            reply = await llm_core.generate_reply(user_id="u5", text="最近失眠口干")

        self.assertIn("微信：laiguo0516", reply)
        self.assertIn("公司地址：新疆乌鲁木齐市 水磨沟区 新民路街道 药材巷30号", reply)


    async def test_required_appendix_is_deduplicated_when_repeated(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        planner_json = (
            '{"tool":"assess_constitution_and_recommend_herbs","arguments":{"query":"sleep issue"},'
            '"confidence":0.95,"reason":"health request"}'
        )
        appendix = "WX: laiguo0516\nCompany: Demo\nAddress: 123 Main St, Newark, DE"
        tool_result = {
            "ok": True,
            "tool": "assess_constitution_and_recommend_herbs",
            "matched_items": [
                {
                    "id": "demo_case",
                    "title": "demo_advice",
                    "advice": "demo",
                    "handoffs": [
                        {"type": "address", "label": "Address", "address": "123 Main St, Newark, DE"}
                    ],
                    "followup_questions": [],
                    "safety": {},
                }
            ],
            "reasons": [],
            "requires_company_append": True,
            "required_append_text": appendix,
        }
        final_with_duplicate_appendix = (
            "????????\n????\n\n????????\n???????\n\n"
            + appendix
            + "\n\n"
            + appendix
        )

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch(
                "app.llm_core.ollama_chat",
                new=AsyncMock(side_effect=[planner_json, final_with_duplicate_appendix]),
            ),
            patch("app.llm_core.execute_tool_call", return_value=tool_result),
        ):
            reply = await llm_core.generate_reply(user_id="u8", text="sleep issue")

        self.assertEqual(reply.count(appendix), 1)


    async def test_symptom_section_is_inserted_before_herbal_section(self) -> None:
        fake_runtime = _FakeRuntime()
        guardrail = GuardrailEngine(fake_runtime.guardrail_settings)
        planner_json = (
            '{"tool":"assess_constitution_and_recommend_herbs","arguments":{"query":"constipation dry mouth"},'
            '"confidence":0.95,"reason":"health request"}'
        )
        tool_result = {
            "ok": True,
            "tool": "assess_constitution_and_recommend_herbs",
            "herbal_recommendations": [
                {
                    "id": "yin_xu_case",
                    "constitution": "yin_xu",
                    "title": "yin_xu_advice",
                    "symptoms": ["constipation", "dry mouth"],
                    "herbs": ["maidong", "yuzhu"],
                    "usage": "daily",
                    "cautions": "",
                }
            ],
            "matched_items": [
                {
                    "id": "yin_xu_case",
                    "title": "yin_xu_advice",
                    "advice": "use maidong and yuzhu",
                    "handoffs": [],
                    "followup_questions": [],
                    "safety": {},
                }
            ],
            "reasons": [],
        }
        final_without_symptom_section = (
            "\u3010\u4f53\u8d28\u503e\u5411\u5206\u6790\u3011\n"
            "\u504f\u9634\u865a\u3002\n\n"
            "\u3010\u4e2d\u836f\u517b\u751f\u5efa\u8bae\u3011\n"
            "\u53ef\u53c2\u8003\u9ea6\u51ac\u3001\u7389\u7af9\u3002"
        )

        with (
            patch("app.llm_core.get_prompt_runtime", return_value=fake_runtime),
            patch("app.llm_core._get_guardrail_engine", return_value=guardrail),
            patch(
                "app.llm_core.ollama_chat",
                new=AsyncMock(side_effect=[planner_json, final_without_symptom_section]),
            ),
            patch("app.llm_core.execute_tool_call", return_value=tool_result),
        ):
            reply = await llm_core.generate_reply(user_id="u7", text="recent constipation and dry mouth")

        self.assertIn("\u3010\u5bf9\u5e94\u75c7\u72b6\u3011", reply)
        self.assertIn("constipation", reply)
        self.assertLess(reply.find("\u3010\u5bf9\u5e94\u75c7\u72b6\u3011"), reply.find("\u3010\u4e2d\u836f\u517b\u751f\u5efa\u8bae\u3011"))


if __name__ == "__main__":
    unittest.main()
