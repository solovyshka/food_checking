import logging
import socket

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def is_allowed(user_id: int | None, settings: Settings | None = None) -> bool:
    if user_id is None:
        return False
    cfg = settings or get_settings()
    allowed = cfg.allowed_user_ids
    return not allowed or user_id in allowed


def make_bot(token: str) -> Bot:
    if not token:
        raise RuntimeError("Telegram bot token is not set")
    # api.telegram.org has broken IPv6 from this host; happy-eyeballs then times out.
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET
    return Bot(token=token, session=session)
