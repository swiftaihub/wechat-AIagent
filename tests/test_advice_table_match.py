import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.tools.advice_table import match_advice_from_table, reload_advice_table


class AdviceTableMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_path = os.environ.get("ADVICE_TABLE_PATH")
        self._old_example_path = os.environ.get("ADVICE_TABLE_EXAMPLE_PATH")
        self._old_max_matches = os.environ.get("ADVICE_TABLE_MAX_MATCHES")

    def tearDown(self) -> None:
        if self._old_path is None:
            os.environ.pop("ADVICE_TABLE_PATH", None)
        else:
            os.environ["ADVICE_TABLE_PATH"] = self._old_path

        if self._old_example_path is None:
            os.environ.pop("ADVICE_TABLE_EXAMPLE_PATH", None)
        else:
            os.environ["ADVICE_TABLE_EXAMPLE_PATH"] = self._old_example_path

        if self._old_max_matches is None:
            os.environ.pop("ADVICE_TABLE_MAX_MATCHES", None)
        else:
            os.environ["ADVICE_TABLE_MAX_MATCHES"] = self._old_max_matches

        try:
            reload_advice_table()
        except FileNotFoundError:
            os.environ.pop("ADVICE_TABLE_PATH", None)
            reload_advice_table()

    def _write_table(self, directory: str, content: str) -> Path:
        path = Path(directory) / "advice_table.private.yaml"
        path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
        os.environ["ADVICE_TABLE_PATH"] = str(path)
        reload_advice_table()
        return path

    def test_keyword_match_returns_item_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_table(
                tmpdir,
                """
                version: 1
                items:
                  - id: veneers
                    title: Veneer Advice
                    keywords: ["veneer", "surface"]
                    triggers:
                      - any: ["veneer"]
                    advice: "Check enamel and bite first."
                    handoffs:
                      - type: contact
                        label: Booking
                        phone: "+1-302-555-0101"
                    followup_questions:
                      - "Is this cosmetic or restorative?"
                    safety:
                      disclaimer: "General info only."
                """,
            )

            result = match_advice_from_table("I want veneer consultation", {"channel": "wechat_mp"})

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["matched_items"]), 1)
            item = result["matched_items"][0]
            self.assertEqual(item["id"], "veneers")
            self.assertEqual(item["handoffs"][0]["phone"], "+1-302-555-0101")

    def test_no_match_returns_empty_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_table(
                tmpdir,
                """
                version: 1
                items:
                  - id: aligner
                    title: Aligner Advice
                    keywords: ["aligner"]
                    triggers:
                      - any: ["aligner"]
                    advice: "Assess bite and spacing."
                    handoffs:
                      - type: questionnaire
                        label: Aligner Form
                        url: "https://example.com/a"
                """,
            )

            result = match_advice_from_table("need veneer plan", {"channel": "wechat_mp"})

            self.assertTrue(result["ok"])
            self.assertEqual(result["matched_items"], [])
            self.assertEqual(result["reasons"], [])

    def test_multiple_matches_are_sorted_stably(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["ADVICE_TABLE_MAX_MATCHES"] = "2"
            self._write_table(
                tmpdir,
                """
                version: 1
                items:
                  - id: a_item
                    title: A Advice
                    keywords: ["smile"]
                    triggers:
                      - any: ["smile"]
                    advice: "A"
                    handoffs:
                      - type: link
                        label: A link
                        url: "https://example.com/a"
                  - id: b_item
                    title: B Advice
                    keywords: ["smile"]
                    triggers:
                      - any: ["smile"]
                    advice: "B"
                    handoffs:
                      - type: link
                        label: B link
                        url: "https://example.com/b"
                """,
            )

            result = match_advice_from_table("smile improvement", {"channel": "wechat_mp"})
            ids = [item["id"] for item in result["matched_items"]]

            self.assertEqual(ids, ["a_item", "b_item"])


if __name__ == "__main__":
    unittest.main()
