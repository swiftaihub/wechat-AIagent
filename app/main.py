import logging

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from app.wechat import router as wechat_router
from app.web_ui import router as web_ui_router
from app.ollama_client import warmup_ollama
from app.prompt_runtime import get_prompt_runtime

app = FastAPI(title="Ollama WeChat MP Gateway")
logger = logging.getLogger(__name__)

app.include_router(wechat_router, prefix="/wechat")
app.include_router(web_ui_router, prefix="/ui")


@app.on_event("startup")
async def validate_prompt_runtime() -> None:
    runtime = get_prompt_runtime()
    logger.info("Prompt config loaded from: %s", runtime.source_path)
    await warmup_ollama()


@app.get("/health")
def health():
    return {"ok": True}
