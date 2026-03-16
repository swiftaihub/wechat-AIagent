import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.llm_provider import warmup_llm_provider
from app.wechat import router as wechat_router
from app.web_ui import router as web_ui_router
from app.product_helper.content import load_catalog_bundle
from app.prompt_runtime import get_prompt_runtime

app = FastAPI(title="Herbal Wellness Tea Product Helper")
logger = logging.getLogger(__name__)

cors_origins_raw = os.getenv("WEBUI_CORS_ALLOWED_ORIGINS", "").strip()
if cors_origins_raw:
    allowed_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=True,
    )
    logger.info("CORS enabled for origins: %s", ",".join(allowed_origins))


def _normalize_base_path(raw: str | None, default: str = "/ui") -> str:
    value = (raw or "").strip()
    if not value:
        return default
    if not value.startswith("/"):
        value = f"/{value}"
    value = value.rstrip("/")
    return value or default


webui_base_path = _normalize_base_path(os.getenv("WEBUI_BASE_PATH", "/ui"))

app.include_router(wechat_router, prefix="/wechat")
app.include_router(web_ui_router, prefix=webui_base_path)
logger.info("Web UI mounted at base path: %s", webui_base_path)


@app.on_event("startup")
async def validate_prompt_runtime() -> None:
    runtime = get_prompt_runtime()
    catalog = load_catalog_bundle()
    logger.info("Prompt config loaded from: %s", runtime.source_path)
    logger.info(
        "Product helper catalog loaded: %d products, %d ingredients, %d articles",
        len(catalog.products),
        len(catalog.ingredients),
        len(catalog.articles),
    )
    await warmup_llm_provider()


@app.get("/health")
def health():
    return {"ok": True}
