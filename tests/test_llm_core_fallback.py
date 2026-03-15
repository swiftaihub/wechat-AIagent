import unittest

from app.llm_core import generate_reply


class LlmCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_reply_for_fatigue(self) -> None:
        reply = await generate_reply(user_id="core-fatigue", text="最近很累，说话都懒，恢复也慢，有没有适合我的茶？")
        self.assertIn("枣曦元气茶", reply)

    async def test_generate_reply_for_high_risk(self) -> None:
        reply = await generate_reply(user_id="core-risk", text="我胸口痛、呼吸困难，喝什么茶比较好？")
        self.assertIn("请尽快", reply)

    async def test_generate_reply_respects_english_preference(self) -> None:
        reply = await generate_reply(
            user_id="core-en",
            text="I want a refined gift tea for my mom.",
            preferred_language="en",
        )
        self.assertTrue(reply)
        self.assertIn("gift", reply.lower())


if __name__ == "__main__":
    unittest.main()
