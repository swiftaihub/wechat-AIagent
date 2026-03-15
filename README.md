# Herbal Wellness Tea Product Helper

Production-oriented FastAPI gateway for a bilingual WeChat and web-based AI helper that recommends premium herbal wellness tea products, explains ingredients, guides article discovery, and supports gifting flows.

This repo has been repositioned from a generic TCM advice assistant into a brand-aligned product helper powered by:
- structured intake
- conservative constitution-leaning inference
- ranked product recommendations
- selective link routing
- compliance-aware guardrails
- short-term session memory

To make deployment reliable, the repo now also contains a bundled storefront data snapshot in [brand_catalog](/d:/Github/app/wechat-AIagent/wechat-AIagent/brand_catalog). Runtime resolution order is:
1. explicit env path
2. sibling `herbal_advice_product_demo` repo
3. bundled `brand_catalog` snapshot

The product, ingredient, and article source of truth comes from the sibling repo `herbal_advice_product_demo`.

## What Changed

The old herbal-advice-table framing has been replaced with a product-helper architecture:

- `app/product_helper/`
  - `config.py`: loaders for questionnaire, constitution scoring, knowledge base, link routing, runtime limits, and commerce guardrails
  - `content.py`: loads products, ingredients, and article metadata from `herbal_advice_product_demo`
  - `intake.py`: structured intake parsing and follow-up policy
  - `intent_router.py`: routes into product recommendation, gifting, ingredient explanation, article guidance, compare, brand-scope FAQ, or high-risk fallback
  - `constitution.py`: conservative ranked constitution tendency inference
  - `recommendation.py`: ranks products with constitution, discomfort, use-case, flavor, premium, and caution logic
  - `links.py`: selects the minimum useful next-step links
  - `session.py`: short-term helper state
  - `service.py`: end-to-end orchestration

- `app/llm_core.py`
  - now routes through the structured product-helper service
  - still keeps prompt-runtime and guardrail compatibility

- `app/tools/constitution_advice.py`
  - repurposed into a backward-compatible wrapper over the new product-helper engine

- `app/web_ui.py` and `app/web_ui_assets/`
  - now load the new questionnaire contract and send product-helper intake payloads

## Source-of-Truth Integration

Default external data paths:

- `..\herbal_advice_product_demo\products.json`
- `..\herbal_advice_product_demo\ingredients.json`
- `..\herbal_advice_product_demo\content\articles`

Override them with:

- `PRODUCT_CATALOG_PATH`
- `INGREDIENT_CATALOG_PATH`
- `ARTICLE_CONTENT_ROOT`

If you deploy via Docker, the bundled `brand_catalog` snapshot is copied into the image automatically, so the helper no longer depends on the sibling repo being mounted at runtime.

## Config Contracts

The repo now expects these conceptual configs:

- `config/prompt.private.yaml`
- `config/questionnaire.private.yaml`
- `config/constitution_scoring.private.yaml`
- `config/herbal_advice.private.yaml`
- `config/link_index.private.yaml`
- `config/runtime_limits.private.yaml`
- `config/guardrail.private.yaml`

Public-safe starter files are committed as:

- `config/prompt.example.yaml`
- `config/questionnaire.example.yaml`
- `config/constitution_scoring.example.yaml`
- `config/herbal_advice.example.yaml`
- `config/link_index.example.yaml`
- `config/runtime_limits.example.yaml`
- `config/guardrail.example.yaml`

Compatibility note:
- `config/questionaire.private.yaml` is kept as a legacy alias for the old misspelled filename.

## Runtime Flow

1. WeChat or web UI sends a message.
2. `app/llm_core.py` resolves language and runs input guardrails.
3. `app/product_helper/service.py`:
   - restores short-term session state
   - parses intake payloads or free-text clues
   - routes intent
   - applies high-risk escalation when needed
   - infers constitution tendencies conservatively
   - ranks 1-3 products max
   - picks only the most useful links
   - renders a concise brand-safe reply
4. Output is trimmed by runtime limits and prompt guardrails.
5. Short-term memory is updated for the next turn.

## Local Setup

1. Create `.env` from `.env.example`.
2. Make sure the sibling repo `herbal_advice_product_demo` exists at the expected location, or override the catalog paths.
3. Install dependencies:

```powershell
.\.venv\Scripts\pip install -r requirements.txt
```

4. Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
```

5. Open the web helper:

- `http://127.0.0.1:8787/ui/herbal_advice`

## Tests

Run the full suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Focused examples:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_product_helper_engine -v
.\.venv\Scripts\python.exe -m unittest tests.test_constitution_advice_tool -v
.\.venv\Scripts\python.exe -m unittest tests.test_web_ui -v
```

## Migration Notes

Old architecture:
- generic TCM advice assistant
- advice table matching
- herbal recommendation dump
- weaker separation between product guidance and educational guidance

New architecture:
- premium bilingual product helper
- product-first recommendation engine
- ingredient/article/gifting routing
- selective link policy
- conservative constitution inference
- dedicated runtime limits and commerce guardrails

Legacy files like `app/tools/advice_table.py` remain only as historical scaffolding and are no longer the primary engine.

## Suggested Deployment Notes

- Keep private config values in local or environment-managed `.private.yaml` files.
- For WeChat, continue using the same `GET /wechat` and `POST /wechat` callback flow.
- For web embedding, use `WEBUI_BASE_PATH` and `WEBUI_CORS_ALLOWED_ORIGINS` as needed.
- If you want the helper links to point at a deployed storefront, update `base_url` in `config/link_index.private.yaml`.
