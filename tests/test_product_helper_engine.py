import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.product_helper.catalog_links import expected_article_links, expected_ingredient_links, expected_product_links
from app.product_helper.config import load_knowledge_base_config, load_link_routing_config
from app.product_helper.content import load_catalog_bundle, reload_catalog_bundle
from app.product_helper.intent_router import classify_domain_intent, route_intent
from app.product_helper.links import select_supporting_links
from app.product_helper.recommendation import rank_products
from app.product_helper.service import get_product_helper_service


def _catalog_json_paths(filename: str) -> tuple[Path, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "brand_catalog" / filename,
        repo_root.parents[1] / "herbal_advice_product_demo" / filename,
    ]
    return tuple(path for path in candidates if path.exists())


def _article_meta_paths() -> tuple[Path, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "brand_catalog" / "articles",
        repo_root.parents[1] / "herbal_advice_product_demo" / "content" / "articles",
    ]
    paths: list[Path] = []
    for root in candidates:
        if not root.exists():
            continue
        paths.extend(sorted(root.glob("*/meta.json")))
    return tuple(paths)


class ProductHelperEngineTests(unittest.TestCase):
    def test_all_product_catalog_files_have_language_aware_links(self) -> None:
        for path in _catalog_json_paths("products.json"):
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(rows, msg=str(path))
            for row in rows:
                slug = row["slug"]
                expected = expected_product_links(slug)
                self.assertEqual(row.get("links"), expected, msg=f"{path}: {slug}")
                self.assertEqual(row.get("buy_link"), expected["en"], msg=f"{path}: {slug}")

    def test_all_ingredient_catalog_files_have_language_aware_links(self) -> None:
        for path in _catalog_json_paths("ingredients.json"):
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(rows, msg=str(path))
            for row in rows:
                slug = row["slug"]
                self.assertEqual(row.get("links"), expected_ingredient_links(slug), msg=f"{path}: {slug}")

    def test_all_article_meta_files_have_language_aware_links(self) -> None:
        for path in _article_meta_paths():
            row = json.loads(path.read_text(encoding="utf-8"))
            slug = row["slug"]
            self.assertEqual(row.get("links"), expected_article_links(slug), msg=str(path))

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

    def test_intent_router_handles_tea_gift_phrase(self) -> None:
        route = route_intent(
            text="想送妈妈一份茶礼，别太苦，体面一点。",
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

    def test_intent_classifier_marks_math_question_out_of_scope(self) -> None:
        route = route_intent(
            text="A skateboard shop has an original price of 80 on a helmet, now 48. What is the percent markdown?",
            language="en",
            knowledge_base=load_knowledge_base_config(),
        )
        classification = classify_domain_intent(
            text="A skateboard shop has an original price of 80 on a helmet, now 48. What is the percent markdown?",
            route=route,
        )

        self.assertEqual(route.intent, "out_of_scope")
        self.assertEqual(classification.label, "OUT_OF_SCOPE")
        self.assertFalse(classification.allowed)

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

    def test_link_selection_returns_language_aware_product_links(self) -> None:
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
        self.assertTrue(all(link.type == "product" for link in links))
        self.assertTrue(all("/zh/products/" in link.url for link in links))

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
        self.assertEqual(result.intent, "product_detail")
        self.assertIn("清露润元茶", result.reply)
        self.assertTrue(any(name in result.reply for name in ("西洋参", "麦冬", "枸杞")))
        self.assertTrue(result.support_links)
        self.assertEqual(result.support_links[0].type, "product")
        self.assertIn("/zh/products/dewlight-replenish-tea", result.support_links[0].url)

    def test_catalog_can_fall_back_to_bundled_snapshot(self) -> None:
        with patch("app.product_helper.content._default_product_repo_root", return_value=Path("Z:/missing-product-site")):
            bundle = reload_catalog_bundle()
        self.assertGreater(len(bundle.products), 0)
        self.assertGreater(len(bundle.ingredients), 0)
        self.assertGreater(len(bundle.articles), 0)
        reload_catalog_bundle()


if __name__ == "__main__":
    unittest.main()
