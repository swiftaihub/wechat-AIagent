from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.product_helper.catalog_links import expected_article_links, expected_ingredient_links, expected_product_links

CATALOG_ROOTS = (
    REPO_ROOT / "brand_catalog",
    WORKSPACE_ROOT / "herbal_advice_product_demo",
)


def _rewrite_catalog(path: Path, *, kind: str) -> int:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a top-level array.")

    changed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug", "")).strip()
        if not slug:
            continue
        expected_links = expected_product_links(slug) if kind == "product" else expected_ingredient_links(slug)
        if row.get("links") != expected_links:
            row["links"] = expected_links
            changed += 1
        if kind == "product" and row.get("buy_link") != expected_links["en"]:
            row["buy_link"] = expected_links["en"]
            changed += 1

    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def _rewrite_article_meta(article_root: Path) -> int:
    changed = 0
    for meta_path in sorted(article_root.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            continue
        slug = str(meta.get("slug", meta_path.parent.name)).strip()
        if not slug:
            continue
        expected_links = expected_article_links(slug)
        if meta.get("links") != expected_links:
            meta["links"] = expected_links
            changed += 1
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    for root in CATALOG_ROOTS:
        products_path = root / "products.json"
        ingredients_path = root / "ingredients.json"
        article_root = root / "articles"
        if root.name == "herbal_advice_product_demo":
            article_root = root / "content" / "articles"
        if not products_path.exists() or not ingredients_path.exists():
            continue
        product_changes = _rewrite_catalog(products_path, kind="product")
        ingredient_changes = _rewrite_catalog(ingredients_path, kind="ingredient")
        article_changes = _rewrite_article_meta(article_root) if article_root.exists() else 0
        print(
            f"{root}: product rows updated={product_changes}, ingredient rows updated={ingredient_changes}, article metas updated={article_changes}"
        )


if __name__ == "__main__":
    main()
