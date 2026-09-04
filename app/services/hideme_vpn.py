"""Bring HideMyName split-VPN up only around OpenAI API calls."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.config import get_settings

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_depth = 0


class HidemeVpnError(RuntimeError):
    pass


def _run(script: str, action: str, conf: str, timeout: float = 90.0) -> None:
    env = os.environ.copy()
    env["HIDEME_OVPN_CONF"] = conf
    if os.geteuid() == 0:
        cmd = [script, action]
    else:
        cmd = ["sudo", "-n", script, action]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if out:
        logger.info("hideme vpn %s: %s", action, out)
    if result.returncode != 0:
        detail = err or out or f"exit {result.returncode}"
        raise HidemeVpnError(f"hideme vpn {action} failed: {detail}")


@asynccontextmanager
async def openai_vpn_session() -> AsyncIterator[None]:
    """Ref-counted VPN session. No-op when HIDEME_VPN_ENABLED=0."""
    global _depth
    settings = get_settings()
    if not settings.hideme_vpn_enabled:
        yield
        return

    script = settings.hideme_vpn_script
    conf = settings.hideme_ovpn_conf
    async with _lock:
        if _depth == 0:
            await asyncio.to_thread(_run, script, "up", conf)
        _depth += 1
    try:
        yield
    finally:
        async with _lock:
            _depth = max(0, _depth - 1)
            if _depth == 0:
                try:
                    await asyncio.to_thread(_run, script, "down", conf)
                except Exception:
                    logger.exception("hideme vpn down failed")
