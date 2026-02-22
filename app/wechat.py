import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from wechatpy import create_reply, parse_message
from wechatpy.exceptions import InvalidSignatureException
from wechatpy.utils import check_signature

from app.llm_core import generate_reply
from app.wechat_token import get_access_token

router = APIRouter()
logger = logging.getLogger(__name__)
DEFAULT_REPLY_TIMEOUT_SECONDS = float(os.getenv("OPENCLAW_REPLY_TIMEOUT_SECONDS", "5"))

WECHAT_SYNC_TIMEOUT_TEXT = os.getenv(
    "WECHAT_SYNC_TIMEOUT_TEXT",
    "\u7cfb\u7edf\u670d\u52a1\u5668\u6b63\u5fd9\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5",
)

WECHAT_SYNC_ERROR_TEXT = os.getenv(
    "WECHAT_SYNC_ERROR_TEXT",
    "\u670d\u52a1\u6682\u65f6\u7e41\u5fd9\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002",
)


def _is_timeout_like_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return True

    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        if exc.response.status_code >= 500:
            return True

    return False


def _validate_wechat_signature(signature: str, timestamp: str, nonce: str) -> None:
    token = os.getenv("WECHAT_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="WECHAT_TOKEN not set")

    try:
        check_signature(token, signature, timestamp, nonce)
    except InvalidSignatureException as exc:
        raise HTTPException(status_code=403, detail="Invalid signature") from exc


@router.post("/menu")
async def create_menu():
    token = await get_access_token()

    menu = {
        "button": [
            {"type": "click", "name": "\u5e2e\u52a9", "key": "HELP"},
            {"type": "click", "name": "\u8bbe\u7f6e", "key": "SETTINGS"},
        ]
    }

    url = "https://api.weixin.qq.com/cgi-bin/menu/create"
    params = {"access_token": token}

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, params=params, json=menu)
        response.raise_for_status()
        data = response.json()

    return data


@router.get("")
async def wechat_verify(signature: str, timestamp: str, nonce: str, echostr: str):
    _validate_wechat_signature(signature, timestamp, nonce)
    return Response(content=echostr, media_type="text/plain")


@router.post("")
async def wechat_message(request: Request, signature: str, timestamp: str, nonce: str):
    _validate_wechat_signature(signature, timestamp, nonce)

    body = await request.body()
    msg = parse_message(body)

    if msg.type != "text":
        return Response(content="success", media_type="text/plain")

    user_text = msg.content.strip()
    from_user = msg.source

    try:
        reply_text = await asyncio.wait_for(
            generate_reply(user_id=from_user, text=user_text),
            timeout=DEFAULT_REPLY_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        if _is_timeout_like_error(exc):
            logger.warning("OpenClaw sync timeout-like failure for user %s: %s", from_user, exc)
            reply_text = WECHAT_SYNC_TIMEOUT_TEXT
        else:
            logger.warning("Failed to generate OpenClaw sync reply for user %s: %s", from_user, exc)
            reply_text = WECHAT_SYNC_ERROR_TEXT

    reply = create_reply(reply_text, msg)
    return Response(content=reply.render(), media_type="application/xml")
