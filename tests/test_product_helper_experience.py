import re
import unittest

from app.product_helper.service import get_product_helper_service


def _url_count(text: str) -> int:
    return len(re.findall(r"https?://", text or ""))


def _has_markdown_link(text: str) -> bool:
    return bool(re.search(r"\[[^\]]+\]\(https?://[^)]+\)", text or ""))


def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s)]+", text or "")


def _has_placeholder_url(text: str) -> bool:
    return "your-store" in (text or "").lower()


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
        for keyword in ("传统用法", "风味", "注意"):
            self.assertIn(keyword, result.reply)
        self.assertNotIn("金菊清眸茶", result.reply)
        self.assertTrue(_has_markdown_link(result.reply))
        self.assertFalse(_has_placeholder_url(result.reply))
        self.assertIn("/zh/products/zaoxi-vitality-tea", result.reply)

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
        if _url_count(result.reply):
            self.assertIn("/zh/ingredients/", result.reply)

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
        self.assertGreaterEqual(_url_count(result.reply), 1)
        self.assertIn("/zh/products/", result.reply)

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
        self.assertTrue(all("/zh/products/" in link.url for link in result.support_links))

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
        self.assertIn("/zh/articles/daily-rituals-for-modern-energy", result.reply)

    def test_english_article_query_returns_en_article_link(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="article-en",
            text="I'd like to read an article about astragalus.",
            preferred_language="en",
            channel="web",
        )

        self.assertEqual(result.intent, "article_request")
        self.assertIn("/en/articles/daily-rituals-for-modern-energy", result.reply)

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
        self.assertLess(len(result.reply), 40)
        self.assertNotIn("我主要帮你看草本茶、原料、送礼和日常 wellness 方向", result.reply)

    def test_math_question_is_refused_and_redirected(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="oos-math-en",
            text="A skateboard shop has an original price of 80 on a helmet, now 48. What is the percent markdown?",
            preferred_language="en",
            channel="web",
        )

        self.assertEqual(result.intent, "out_of_scope")
        self.assertIn("tea", result.reply.lower())
        self.assertIn("wellness", result.reply.lower())
        self.assertNotIn("40%", result.reply)
        self.assertNotIn("0.4", result.reply)

    def test_recommendation_reply_is_not_overlong(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="rec-zh",
            text="如果我最近口干又熬夜，哪款更合适？",
            channel="web",
        )

        self.assertEqual(result.intent, "symptom_or_discomfort_guidance")
        self.assertLessEqual(len(result.reply), 1800)
        self.assertNotRegex(result.reply, r"(?:\.\.\.|…)\s*$")
        self.assertRegex(result.reply[-1:], r"[。！？.!?)]$")

    def test_english_detail_and_gifting_queries_work(self) -> None:
        service = get_product_helper_service()
        detail = service.handle(
            user_id="detail-en",
            text="What ingredients are in Red Date Dawn Vitality Tea?",
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
        self.assertIn("Nutrition focus", detail.reply)
        self.assertTrue(_has_markdown_link(detail.reply))
        self.assertFalse(_has_placeholder_url(detail.reply))
        self.assertIn("/en/products/zaoxi-vitality-tea", detail.reply)
        self.assertEqual(gift.intent, "gifting_recommendation")
        self.assertIn("gift", gift.reply.lower())
        self.assertIn("handwritten gift card message", gift.reply.lower())
        self.assertIn("/en/products/", gift.reply)

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
        self.assertIn("传统用法", followup.reply)
        self.assertTrue(_has_markdown_link(followup.reply))
        self.assertFalse(_has_placeholder_url(followup.reply))
        self.assertIn("/zh/products/", followup.reply)
        self.assertNotIn("我会先从", followup.reply)

    def test_ingredient_explanation_returns_language_matched_link(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="ingredient-link-en",
            text="Tell me more about astragalus.",
            preferred_language="en",
            channel="web",
        )

        self.assertEqual(result.intent, "ingredient_explanation")
        self.assertIn("Astragalus Root", result.reply)
        self.assertIn("/en/ingredients/astragalus", result.reply)

    def test_gift_followup_confirmation_generates_card_message(self) -> None:
        service = get_product_helper_service()
        first = service.handle(
            user_id="gift-card-zh",
            text="送妈妈的话你会选哪款？",
            channel="web",
        )
        followup = service.handle(
            user_id="gift-card-zh",
            text="需要",
            channel="web",
        )

        self.assertEqual(first.intent, "gifting_recommendation")
        self.assertIn("手写卡片文案", first.reply)
        self.assertEqual(followup.mode, "pending_action_resolution")
        self.assertIn("卡片文案", followup.reply)
        self.assertNotIn("我主要帮你", followup.reply)

    def test_english_confirmation_words_resolve_pending_action(self) -> None:
        service = get_product_helper_service()
        first = service.handle(
            user_id="gift-card-en",
            text="Can you recommend a gift tea for my mom that is not too bitter?",
            preferred_language="en",
            channel="web",
        )
        followup = service.handle(
            user_id="gift-card-en",
            text="ok",
            preferred_language="en",
            channel="web",
        )

        self.assertEqual(first.intent, "gifting_recommendation")
        self.assertIn("gift card message", first.reply.lower())
        self.assertEqual(followup.mode, "pending_action_resolution")
        self.assertIn("handwritten card version", followup.reply.lower())
        self.assertNotIn("I mainly help", followup.reply)

    def test_long_ingredient_explanation_stays_complete(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="long-detail-en",
            text="What ingredients are in Red Date Dawn Vitality Tea?",
            preferred_language="en",
            channel="web",
        )

        self.assertGreater(len(result.reply), 1200)
        self.assertIn("Overall, it leans toward", result.reply)
        self.assertNotRegex(result.reply, r"(?:\.\.\.|…)\s*$")
        self.assertRegex(result.reply[-1:], r"[.!?)]$")

    def test_greeting_stays_natural_without_forcing_recommendation(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="greeting-en",
            text="hi",
            preferred_language="en",
            channel="web",
        )

        self.assertEqual(result.intent, "general_brand_scope_qna")
        self.assertIn("Hi", result.reply)
        self.assertNotIn("I would start with", result.reply)


if __name__ == "__main__":
    unittest.main()
