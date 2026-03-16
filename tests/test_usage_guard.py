import os
import unittest
from unittest.mock import patch

from app.metrics import reset_runtime_metrics
from app.runtime_config import reset_runtime_config_cache
from app.usage_guard import get_usage_guard, reset_usage_guard_cache


def _reset_caches() -> None:
    reset_runtime_config_cache()
    reset_usage_guard_cache()
    reset_runtime_metrics()


class UsageGuardTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        _reset_caches()

    async def test_short_window_rate_limit_blocks_user(self) -> None:
        env = {
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "RATE_LIMIT_MAX_REQUESTS": "2",
            "RAPID_ABUSE_BLOCK_MINUTES": "30",
            "MAX_REQUESTS_PER_HOUR": "50",
            "MAX_REQUESTS_PER_DAY": "200",
            "MAX_MESSAGES_PER_USER_SESSION": "20",
        }
        with patch.dict(os.environ, env, clear=False):
            _reset_caches()
            guard = get_usage_guard()
            first = await guard.admit_request(user_id="rate-user", text="first", preferred_language="en")
            await guard.release(first.lease)
            second = await guard.admit_request(user_id="rate-user", text="second", preferred_language="en")
            await guard.release(second.lease)
            blocked = await guard.admit_request(user_id="rate-user", text="third", preferred_language="en")

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.rejection.code, "RATE_LIMITED")
        self.assertGreater(blocked.rejection.retry_after_seconds, 0)

    async def test_session_ceiling_blocks_after_configured_turns(self) -> None:
        env = {
            "RATE_LIMIT_MAX_REQUESTS": "50",
            "MAX_REQUESTS_PER_HOUR": "50",
            "MAX_REQUESTS_PER_DAY": "200",
            "MAX_MESSAGES_PER_USER_SESSION": "3",
            "USER_SESSION_COOLDOWN_MINUTES": "120",
        }
        with patch.dict(os.environ, env, clear=False):
            _reset_caches()
            guard = get_usage_guard()
            for index in range(3):
                admission = await guard.admit_request(user_id="session-user", text=f"msg-{index}", preferred_language="en")
                self.assertTrue(admission.allowed)
                await guard.release(admission.lease)
            blocked = await guard.admit_request(user_id="session-user", text="msg-4", preferred_language="en")

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.rejection.code, "SESSION_LIMIT_REACHED")
        self.assertGreaterEqual(blocked.rejection.retry_after_seconds, 60)

    async def test_hourly_and_daily_quota_blocks_are_distinct(self) -> None:
        hourly_env = {
            "RATE_LIMIT_MAX_REQUESTS": "50",
            "MAX_REQUESTS_PER_HOUR": "2",
            "MAX_REQUESTS_PER_DAY": "10",
            "MAX_MESSAGES_PER_USER_SESSION": "20",
        }
        with patch.dict(os.environ, hourly_env, clear=False):
            _reset_caches()
            guard = get_usage_guard()
            for text in ("a", "b"):
                admission = await guard.admit_request(user_id="quota-hour", text=text, preferred_language="en")
                self.assertTrue(admission.allowed)
                await guard.release(admission.lease)
            blocked_hour = await guard.admit_request(user_id="quota-hour", text="c", preferred_language="en")

        self.assertFalse(blocked_hour.allowed)
        self.assertEqual(blocked_hour.rejection.code, "HOURLY_QUOTA_EXCEEDED")

        daily_env = {
            "RATE_LIMIT_MAX_REQUESTS": "50",
            "MAX_REQUESTS_PER_HOUR": "50",
            "MAX_REQUESTS_PER_DAY": "2",
            "MAX_MESSAGES_PER_USER_SESSION": "20",
        }
        with patch.dict(os.environ, daily_env, clear=False):
            _reset_caches()
            guard = get_usage_guard()
            for text in ("a", "b"):
                admission = await guard.admit_request(user_id="quota-day", text=text, preferred_language="en")
                self.assertTrue(admission.allowed)
                await guard.release(admission.lease)
            blocked_day = await guard.admit_request(user_id="quota-day", text="c", preferred_language="en")

        self.assertFalse(blocked_day.allowed)
        self.assertEqual(blocked_day.rejection.code, "DAILY_QUOTA_EXCEEDED")

    async def test_concurrent_request_is_blocked(self) -> None:
        env = {
            "RATE_LIMIT_MAX_REQUESTS": "50",
            "MAX_REQUESTS_PER_HOUR": "50",
            "MAX_REQUESTS_PER_DAY": "200",
            "MAX_MESSAGES_PER_USER_SESSION": "20",
        }
        with patch.dict(os.environ, env, clear=False):
            _reset_caches()
            guard = get_usage_guard()
            first = await guard.admit_request(user_id="concurrent-user", text="hello", preferred_language="en")
            second = await guard.admit_request(user_id="concurrent-user", text="another", preferred_language="en")
            await guard.release(first.lease)

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(second.rejection.code, "CONCURRENT_REQUEST_BLOCKED")

    async def test_repeated_identical_prompt_triggers_abuse_block(self) -> None:
        env = {
            "RATE_LIMIT_MAX_REQUESTS": "50",
            "MAX_REQUESTS_PER_HOUR": "50",
            "MAX_REQUESTS_PER_DAY": "200",
            "MAX_MESSAGES_PER_USER_SESSION": "20",
            "REPEAT_PROMPT_MAX_DUPLICATES": "3",
            "REPEAT_PROMPT_WINDOW_SECONDS": "300",
        }
        with patch.dict(os.environ, env, clear=False):
            _reset_caches()
            guard = get_usage_guard()
            for _ in range(2):
                admission = await guard.admit_request(user_id="abuse-user", text="same prompt", preferred_language="en")
                self.assertTrue(admission.allowed)
                await guard.release(admission.lease)
            blocked = await guard.admit_request(user_id="abuse-user", text="same prompt", preferred_language="en")

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.rejection.code, "ABUSE_BLOCKED")

    async def test_usage_limit_message_can_be_overridden_from_env(self) -> None:
        env = {
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "RATE_LIMIT_MAX_REQUESTS": "1",
            "RAPID_ABUSE_BLOCK_MINUTES": "30",
            "MAX_REQUESTS_PER_HOUR": "50",
            "MAX_REQUESTS_PER_DAY": "200",
            "MAX_MESSAGES_PER_USER_SESSION": "20",
            "USAGE_LIMIT_MESSAGE_ZH": "超出使用限制，请稍后再访问。",
            "USAGE_LIMIT_MESSAGE_EN": "Usage limit reached. Please try again later.",
        }
        with patch.dict(os.environ, env, clear=False):
            _reset_caches()
            guard = get_usage_guard()
            first = await guard.admit_request(user_id="override-en", text="first", preferred_language="en")
            await guard.release(first.lease)
            blocked_en = await guard.admit_request(user_id="override-en", text="second", preferred_language="en")

            second = await guard.admit_request(user_id="override-zh", text="first", preferred_language="zh")
            await guard.release(second.lease)
            blocked_zh = await guard.admit_request(user_id="override-zh", text="second", preferred_language="zh")

        self.assertFalse(blocked_en.allowed)
        self.assertEqual(blocked_en.rejection.user_message, "Usage limit reached. Please try again later.")
        self.assertFalse(blocked_zh.allowed)
        self.assertEqual(blocked_zh.rejection.user_message, "超出使用限制，请稍后再访问。")


if __name__ == "__main__":
    unittest.main()
