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


def make_bot(token: str, settings: Settings | None = None) -> Bot:
    if not token:
        raise RuntimeError("Telegram bot token is not set")
    cfg = settings or get_settings()
    proxy = (cfg.telegram_proxy_url or "").strip() or None
    # api.telegram.org has broken IPv6 from this host; happy-eyeballs then times out.
    session = AiohttpSession(proxy=proxy)
    session._connector_init["family"] = socket.AF_INET
    if proxy:
        logger.info("Telegram API via proxy %s", proxy)
    return Bot(token=token, session=session)
