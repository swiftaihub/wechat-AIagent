import re
import unittest

from app.product_helper.service import get_product_helper_service


def _url_count(text: str) -> int:
    return len(re.findall(r"https?://", text or ""))


class ProductHelperExperienceTests(unittest.TestCase):
    def test_product_detail_question_gets_direct_answer(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="detail-zh",
            text="枣曦元气茶里面都有什么原材料？",
            channel="web",
        )

        self.assertEqual(result.intent, "product_detail")
        for keyword in ("黄芪", "红枣", "枸杞", "陈皮"):
            self.assertIn(keyword, result.reply)
        self.assertNotIn("金菊清眸茶", result.reply)
        self.assertEqual(_url_count(result.reply), 0)

    def test_ingredient_pair_question_gets_direct_answer(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="pair-zh",
            text="陈皮和玫瑰为什么经常搭配？",
            channel="web",
        )

        self.assertEqual(result.intent, "ingredient_explanation")
        self.assertIn("陈皮", result.reply)
        self.assertIn("玫瑰", result.reply)
        self.assertNotIn("推荐", result.reply)
        self.assertLessEqual(_url_count(result.reply), 1)

    def test_gifting_question_behaves_naturally(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="gift-zh",
            text="送妈妈的话你会选哪款？",
            channel="web",
        )

        self.assertEqual(result.intent, "gifting_recommendation")
        self.assertIn("送妈妈", result.reply)
        self.assertNotIn("我可以帮你做产品挑选", result.reply)
        self.assertLessEqual(_url_count(result.reply), 2)

    def test_compare_question_compares_named_products(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="compare-zh",
            text="月露夜养茶和清露润元茶有什么区别？",
            channel="web",
        )

        self.assertEqual(result.intent, "compare_products")
        self.assertIn("月露夜养茶", result.reply)
        self.assertIn("清露润元茶", result.reply)
        self.assertNotIn("枣曦元气茶", result.reply)
        self.assertEqual({link.slug for link in result.support_links}, {"moon-dew-night-restore-tea", "dewlight-replenish-tea"})

    def test_article_query_about_ingredient_prefers_article_guidance(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="article-zh",
            text="我想看看关于黄芪的文章",
            channel="web",
        )

        self.assertEqual(result.intent, "article_request")
        self.assertIn("现代节奏中的日常元气仪式", result.reply)
        self.assertNotIn("黄芪 放在草本茶里", result.reply)
        self.assertEqual(_url_count(result.reply), 1)

    def test_wellness_education_stays_in_scope_without_followup(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="edu-zh",
            text="气虚和平时容易累有什么关系？",
            channel="web",
        )

        self.assertEqual(result.intent, "wellness_education_in_scope")
        self.assertFalse(result.needs_followup)
        self.assertIn("不等同于诊断", result.reply)
        self.assertNotIn("如果只补一个信息", result.reply)

    def test_out_of_scope_query_redirects_politely(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="oos-zh",
            text="帮我写一个 Python Docker 部署脚本",
            channel="web",
        )

        self.assertEqual(result.intent, "out_of_scope")
        self.assertIn("草本茶", result.reply)
        self.assertIn("编程", result.reply)

    def test_recommendation_reply_is_not_overlong(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="rec-zh",
            text="如果我最近口干又熬夜，哪款更合适？",
            channel="web",
        )

        self.assertEqual(result.intent, "symptom_or_discomfort_guidance")
        self.assertLessEqual(len(result.reply), 420)

    def test_english_detail_and_gifting_queries_work(self) -> None:
        service = get_product_helper_service()
        detail = service.handle(
            user_id="detail-en",
            text="What is in Red Date Dawn Vitality Tea?",
            preferred_language="en",
            channel="web",
        )
        gift = service.handle(
            user_id="gift-en",
            text="Can you recommend a gift tea for my mom that is not too bitter?",
            preferred_language="en",
            channel="web",
        )

        self.assertEqual(detail.intent, "product_detail")
        self.assertIn("Astragalus Root", detail.reply)
        self.assertEqual(gift.intent, "gifting_recommendation")
        self.assertIn("gift", gift.reply.lower())

    def test_contextual_product_followup_uses_session_shortlist(self) -> None:
        service = get_product_helper_service()
        first = service.handle(
            user_id="context-followup",
            text="最近很累，说话都懒，恢复也慢，有没有适合我的茶？",
            channel="web",
        )
        followup = service.handle(
            user_id="context-followup",
            text="这款里面都有什么原料？",
            channel="web",
        )

        self.assertEqual(first.intent, "symptom_or_discomfort_guidance")
        self.assertEqual(followup.intent, "product_detail")
        self.assertIn("黄芪", followup.reply)
        self.assertNotIn("我会先从", followup.reply)


if __name__ == "__main__":
    unittest.main()
