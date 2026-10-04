"""Call a Grok Bot routine webhook. A 200 only means the run started."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx

TEST_MESSAGE = "Проверка связи из дневника еды. Разбор ещё не включён."
# FIREBAT xray HTTP proxy: box → RU → France. Not the old tunnel on 8888.
DEFAULT_PROXY = "http://127.0.0.1:10809"


class GrokNotConfigured(RuntimeError):
    pass


class GrokCallFailed(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    url = os.environ.get("GROK_BOT_WEBHOOK_URL", "").strip()
    key = os.environ.get("GROK_BOT_WEBHOOK_KEY", "").strip()
    if not url.startswith("https://") or not key:
        raise GrokNotConfigured("Вызов Grok ещё не настроен на коробке")
    return url, key


def proxy_url() -> str:
    configured = os.environ.get("GROK_BOT_PROXY_URL", "").strip()
    return configured or DEFAULT_PROXY


def callback_base() -> str:
    value = os.environ.get("FOOD_GROK_CALLBACK_BASE_URL", "https://food-consumption.solovyshka.com").rstrip("/")
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise GrokNotConfigured("Не настроен HTTPS-адрес для возврата результата Grok")
    return value


def check_analysis_config() -> None:
    _credentials()
    callback_base()


async def send_analysis(payload: dict) -> None:
    url, key = _credentials()
    # Do not retry a webhook automatically: a lost response may already have started a run.
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, proxy=proxy_url()) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
    except httpx.HTTPError as exc:
        raise GrokDispatchUnknown("Ответ Grok не получен. Проверяем, начался ли разбор") from exc
    if response.status_code != 200:
        raise GrokCallFailed("Grok не принял задачу разбора")


class GrokDispatchUnknown(GrokCallFailed):
    pass


async def send_test(text: str = "", *, has_image: bool = False) -> dict:
    url, key = _credentials()
    message = text.strip() or TEST_MESSAGE
    payload = {
        "kind": "test",
        "source": "food_checking",
        "message": message[:2000],
        "has_image": has_image,
    }
    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=False, proxy=proxy_url()
        ) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise GrokCallFailed("Не удалось отправить тестовое сообщение") from exc
    if response.status_code != 200:
        raise GrokCallFailed("Grok не принял сообщение")
    return {"accepted": True, "message": payload["message"]}
