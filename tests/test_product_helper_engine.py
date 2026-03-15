import unittest
from pathlib import Path
from unittest.mock import patch

from app.product_helper.config import load_knowledge_base_config, load_link_routing_config
from app.product_helper.content import load_catalog_bundle, reload_catalog_bundle
from app.product_helper.intent_router import route_intent
from app.product_helper.links import select_supporting_links
from app.product_helper.recommendation import rank_products
from app.product_helper.service import get_product_helper_service


class ProductHelperEngineTests(unittest.TestCase):
    def test_intent_router_handles_gifting(self) -> None:
        route = route_intent(
            text="想给妈妈买一个体面一点的养生茶礼物，不想太苦。",
            language="zh",
            knowledge_base=load_knowledge_base_config(),
        )
        self.assertEqual(route.intent, "gifting_recommendation")
        self.assertEqual(route.use_case, "gifting")

    def test_intent_router_handles_flexible_gifting_phrase(self) -> None:
        route = route_intent(
            text="送给女生什么茶好？",
            language="zh",
            knowledge_base=load_knowledge_base_config(),
        )
        self.assertEqual(route.intent, "gifting_recommendation")
        self.assertEqual(route.use_case, "gifting")

    def test_intent_router_handles_product_catalog_request(self) -> None:
        route = route_intent(
            text="把产品列表给我看一下",
            language="zh",
            knowledge_base=load_knowledge_base_config(),
        )
        self.assertEqual(route.intent, "product_catalog_request")

    def test_intent_router_handles_direct_tea_question(self) -> None:
        route = route_intent(
            text="女生适合什么茶？",
            language="zh",
            knowledge_base=load_knowledge_base_config(),
        )
        self.assertEqual(route.intent, "product_recommendation_direct")

    def test_intent_router_handles_ingredient_question(self) -> None:
        route = route_intent(
            text="冬虫夏草适合什么样的人？",
            language="zh",
            knowledge_base=load_knowledge_base_config(),
        )
        self.assertEqual(route.intent, "ingredient_explanation")
        self.assertIn("cordyceps", route.mentioned_ingredients)

    def test_recommendation_ranking_prefers_dryness_products_for_dryness_query(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="dry-engine",
            text="I've been staying up late and feeling dry lately. What would fit me best?",
            preferred_language="en",
            channel="web",
        )
        self.assertTrue(result.product_recommendations)
        top_slugs = [item.product.slug for item in result.product_recommendations]
        self.assertIn(top_slugs[0], {"dewlight-replenish-tea", "moon-dew-night-restore-tea"})

    def test_link_selection_prefers_product_links_for_purchase_like_intent(self) -> None:
        bundle = load_catalog_bundle()
        service = get_product_helper_service()
        result = service.handle(
            user_id="link-user",
            text="最近很累，说话都懒，恢复也慢，有没有适合我的茶？",
            channel="web",
        )
        links = select_supporting_links(
            bundle=bundle,
            config=load_link_routing_config(),
            language="zh",
            intent=result.intent,
            use_case="recent_discomfort_guidance",
            product_recommendations=result.product_recommendations,
            mentioned_products=(),
            mentioned_ingredients=(),
        )
        self.assertTrue(links)
        self.assertEqual(links[0].type, "product")

    def test_compare_products_keeps_named_products_in_focus(self) -> None:
        service = get_product_helper_service()
        bundle = load_catalog_bundle()
        first = bundle.products_by_slug["crimson-gold-radiance-tea"].name["zh"]
        second = bundle.products_by_slug["gilded-cordyceps-reserve-tea"].name["zh"]
        result = service.handle(
            user_id="compare-user",
            text=f"{first}和{second}有什么区别？",
            channel="web",
        )
        self.assertEqual(result.intent, "compare_products")
        self.assertIn(first, result.reply)
        self.assertIn(second, result.reply)

    def test_service_handles_flexible_gifting_question(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="gift-flex-user",
            text="送给女生什么茶好？",
            channel="web",
        )
        self.assertEqual(result.intent, "gifting_recommendation")
        self.assertNotIn("我可以帮你做产品挑选", result.reply)
        self.assertTrue(any(name in result.reply for name in ("绯", "清露", "Rosy Glow", "Crimson Gold", "Dewlight")))

    def test_service_can_render_product_catalog(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="catalog-user",
            text="有哪些茶？把产品列表给我看一下。",
            channel="web",
        )
        self.assertEqual(result.intent, "product_catalog_request")
        self.assertIn("茶单", result.reply)
        self.assertTrue(result.support_links)
        self.assertEqual(result.support_links[0].type, "collection")

    def test_service_can_explain_ingredients_for_selected_product(self) -> None:
        service = get_product_helper_service()
        result = service.handle(
            user_id="ingredient-product-user",
            text='用户基础信息（product_helper intake）：\n```json\n{"use_case":"ingredient_learning","selected_product_slug":"dewlight-replenish-tea"}\n```',
            channel="web",
        )
        self.assertEqual(result.intent, "product_ingredient_breakdown")
        self.assertIn("清露润元茶", result.reply)
        self.assertTrue(any(name in result.reply for name in ("西洋参", "麦冬", "枸杞")))
        self.assertTrue(result.support_links)
        self.assertEqual(result.support_links[0].type, "product")

    def test_catalog_can_fall_back_to_bundled_snapshot(self) -> None:
        with patch("app.product_helper.content._default_product_repo_root", return_value=Path("Z:/missing-product-site")):
            bundle = reload_catalog_bundle()
        self.assertGreater(len(bundle.products), 0)
        self.assertGreater(len(bundle.ingredients), 0)
        self.assertGreater(len(bundle.articles), 0)
        reload_catalog_bundle()


if __name__ == "__main__":
    unittest.main()
