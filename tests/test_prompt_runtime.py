import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.prompt_runtime import get_prompt_runtime, reload_prompt_runtime


class PromptRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_prompt_path = os.environ.get("PROMPT_CONFIG_PATH")
        self._original_example_path = os.environ.get("PROMPT_EXAMPLE_PATH")

    def tearDown(self) -> None:
        if self._original_prompt_path is None:
            os.environ.pop("PROMPT_CONFIG_PATH", None)
        else:
            os.environ["PROMPT_CONFIG_PATH"] = self._original_prompt_path

        if self._original_example_path is None:
            os.environ.pop("PROMPT_EXAMPLE_PATH", None)
        else:
            os.environ["PROMPT_EXAMPLE_PATH"] = self._original_example_path

        get_prompt_runtime.cache_clear()

    def test_load_runtime_from_env_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "prompt.private.yaml"
            cfg.write_text(
                textwrap.dedent(
                    """
                    default_profile: wechat
                    profiles:
                      wechat:
                        system_prompt: |
                          PRIVATE_SYSTEM_PLACEHOLDER
                        user_prompt_template: |
                          USER={user_id}
                          CHANNEL={channel}
                          MESSAGE={user_text}
                    guardrail:
                      enabled: true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            os.environ["PROMPT_CONFIG_PATH"] = str(cfg)
            runtime = reload_prompt_runtime()

            self.assertEqual(runtime.default_profile, "wechat")
            self.assertIn("PRIVATE_SYSTEM_PLACEHOLDER", runtime.system_prompt("wechat"))

            rendered = runtime.render_user_prompt(
                profile="wechat",
                user_text="hello",
                user_id="u1",
                context={"channel": "wechat_mp"},
            )
            self.assertIn("USER=u1", rendered)
            self.assertIn("CHANNEL=wechat_mp", rendered)
            self.assertIn("MESSAGE=hello", rendered)

    def test_localized_profile_and_guardrail_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "prompt.private.yaml"
            cfg.write_text(
                textwrap.dedent(
                    """
                    default_profile: wechat
                    profiles:
                      wechat:
                        system_prompt:
                          zh: |
                            系统中文
                          en: |
                            SYSTEM_EN
                        user_prompt_template:
                          zh: |
                            用户={user_text}
                          en: |
                            USER={user_text}
                    guardrail:
                      enabled: true
                      blocked_response:
                        zh: 中文拦截
                        en: English blocked
                      fallback_response:
                        zh: 中文兜底
                        en: English fallback
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            os.environ["PROMPT_CONFIG_PATH"] = str(cfg)
            runtime = reload_prompt_runtime()

            self.assertEqual(runtime.system_prompt("wechat", language="zh"), "系统中文")
            self.assertEqual(runtime.system_prompt("wechat", language="en"), "SYSTEM_EN")
            self.assertEqual(
                runtime.render_user_prompt(profile="wechat", user_text="hello", language="en"),
                "USER=hello",
            )

            guardrail_en = runtime.guardrail_settings_for_language("en")
            guardrail_zh = runtime.guardrail_settings_for_language("zh")
            self.assertEqual(guardrail_en.blocked_response, "English blocked")
            self.assertEqual(guardrail_en.fallback_response, "English fallback")
            self.assertEqual(guardrail_zh.blocked_response, "中文拦截")

    def test_missing_template_variable_falls_back_to_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "prompt.private.yaml"
            cfg.write_text(
                textwrap.dedent(
                    """
                    default_profile: wechat
                    profiles:
                      wechat:
                        system_prompt: |
                          SYSTEM
                        user_prompt_template: |
                          A={unknown}
                          B={user_text}
                    guardrail:
                      enabled: true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            os.environ["PROMPT_CONFIG_PATH"] = str(cfg)
            runtime = reload_prompt_runtime()

            rendered = runtime.render_user_prompt(profile="wechat", user_text="x")
            self.assertIn("A=", rendered)
            self.assertIn("B=x", rendered)

    def test_load_runtime_from_local_private_config(self) -> None:
        if os.environ.get("RUN_PRIVATE_PROMPT_TEST") != "1":
            self.skipTest("Set RUN_PRIVATE_PROMPT_TEST=1 to enable local private prompt test.")

        repo_root = Path(__file__).resolve().parent.parent
        local_cfg = repo_root / "config" / "prompt.private.yaml"
        self.assertTrue(local_cfg.exists(), "Missing local config: config/prompt.private.yaml")

        os.environ["PROMPT_CONFIG_PATH"] = str(local_cfg)
        runtime = reload_prompt_runtime()

        self.assertEqual(runtime.source_path.resolve(), local_cfg.resolve())
        self.assertTrue(runtime.default_profile)

        system_prompt = runtime.system_prompt(runtime.default_profile)
        self.assertTrue(system_prompt.strip())

        rendered = runtime.render_user_prompt(
            profile=runtime.default_profile,
            user_text="local config smoke test",
            user_id="local-user",
            context={"channel": "wechat_mp"},
        )
        self.assertIn("local config smoke test", rendered)
        self.assertTrue(rendered.strip())


if __name__ == "__main__":
    unittest.main()
