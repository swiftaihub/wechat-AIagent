import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:32b-instruct-q4_K_M")
OPENCLAW_REPLY_TIMEOUT_SECONDS = float(os.getenv("OPENCLAW_REPLY_TIMEOUT_SECONDS", "5"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "180"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))
OLLAMA_TOP_P = float(os.getenv("OLLAMA_TOP_P", "0.9"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_WARMUP_ON_STARTUP = os.getenv("OLLAMA_WARMUP_ON_STARTUP", "1").strip() not in {
    "0",
    "false",
    "False",
}
OLLAMA_WARMUP_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_WARMUP_TIMEOUT_SECONDS", "15"))


def _build_payload(model: str, prompt: str, response_format: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": OLLAMA_NUM_PREDICT,
            "temperature": OLLAMA_TEMPERATURE,
            "top_p": OLLAMA_TOP_P,
        },
    }
    if OLLAMA_KEEP_ALIVE:
        payload["keep_alive"] = OLLAMA_KEEP_ALIVE
    if response_format:
        payload["format"] = response_format
    return payload


async def ollama_chat(*, system_prompt: str, user_prompt: str, response_format: str | None = None) -> str:
    final_prompt = f"{system_prompt.strip()}\n\n{user_prompt.strip()}".strip()
    active_model = OLLAMA_MODEL.strip()
    payload = _build_payload(active_model, final_prompt, response_format=response_format)
    url = f"{OLLAMA_BASE_URL}/api/generate"

    async with httpx.AsyncClient(timeout=OPENCLAW_REPLY_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    return (data.get("response") or "").strip() or "I could not generate a valid reply."


async def warmup_ollama(model: str | None = None) -> None:
    if not OLLAMA_WARMUP_ON_STARTUP:
        return

    active_model = (model or OLLAMA_MODEL).strip()
    payload = _build_payload(active_model, "warmup")
    payload["options"]["num_predict"] = 8
    url = f"{OLLAMA_BASE_URL}/api/generate"

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_WARMUP_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info("Ollama warmup succeeded for model: %s", active_model)
    except Exception as exc:
        logger.warning("Ollama warmup failed for model %s: %s", active_model, exc)
