import unittest

from app.memory_store import ConversationMemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_add_exchange_and_render_history(self) -> None:
        store = ConversationMemoryStore(
            enabled=True,
            max_turns=3,
            ttl_seconds=1800,
            max_message_chars=200,
            max_history_chars=800,
        )
        store.add_exchange(user_id="u1", user_text="hello", assistant_text="hi there", now_ts=1_000)
        history = store.render_history_block(user_id="u1", now_ts=1_001)

        self.assertIn("[User] hello", history)
        self.assertIn("[Assistant] hi there", history)

    def test_ttl_prunes_old_messages(self) -> None:
        store = ConversationMemoryStore(
            enabled=True,
            max_turns=3,
            ttl_seconds=60,
            max_message_chars=200,
            max_history_chars=800,
        )
        store.add_exchange(user_id="u1", user_text="old", assistant_text="old-reply", now_ts=100)
        history_after_ttl = store.render_history_block(user_id="u1", now_ts=200)

        self.assertEqual(history_after_ttl, "")

    def test_max_turns_keeps_recent_only(self) -> None:
        store = ConversationMemoryStore(
            enabled=True,
            max_turns=2,
            ttl_seconds=1800,
            max_message_chars=200,
            max_history_chars=800,
        )
        store.add_exchange(user_id="u1", user_text="q1", assistant_text="a1", now_ts=10)
        store.add_exchange(user_id="u1", user_text="q2", assistant_text="a2", now_ts=20)
        store.add_exchange(user_id="u1", user_text="q3", assistant_text="a3", now_ts=30)

        history = store.render_history_block(user_id="u1", now_ts=31)
        self.assertNotIn("q1", history)
        self.assertNotIn("a1", history)
        self.assertIn("q2", history)
        self.assertIn("a2", history)
        self.assertIn("q3", history)
        self.assertIn("a3", history)

    def test_recent_messages_and_rebuild_window_drop_last_assistant(self) -> None:
        store = ConversationMemoryStore(
            enabled=True,
            max_turns=3,
            ttl_seconds=1800,
            max_message_chars=200,
            max_history_chars=800,
        )
        store.add_exchange(user_id="u1", user_text="q1", assistant_text="a1", now_ts=10)
        store.add_exchange(user_id="u1", user_text="q2", assistant_text="a2", now_ts=20)

        messages = store.recent_messages(user_id="u1", now_ts=21)
        self.assertEqual([message.role for message in messages], ["user", "assistant", "user", "assistant"])

        rebuilt = store.rebuild_window(user_id="u1", keep_turns=2, drop_last_assistant=True, now_ts=21)
        self.assertEqual([message.role for message in rebuilt], ["user", "assistant", "user"])
        self.assertEqual(rebuilt[-1].content, "q2")


if __name__ == "__main__":
    unittest.main()
