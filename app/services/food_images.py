"""Publish a bounded, expiring copy to the OVH media service, never to the APK host folder."""
import hashlib
import os
import re
from urllib.parse import urlsplit

import httpx
from app.services.grok_bot import GrokCallFailed, GrokNotConfigured, proxy_url


def configuration():
    base = os.environ.get('FOOD_IMAGE_HOST_URL', 'https://food-consumption.solovyshka.com').rstrip('/')
    parsed = urlsplit(base)
    token = os.environ.get('FOOD_IMAGE_HOST_TOKEN', '')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or len(token) < 32:
        raise GrokNotConfigured('Временное хранение фото на OVH пока не настроено')
    return base, token


async def publish_image(data: bytes):
    base, token = configuration()
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False, proxy=proxy_url()) as client:
            response = await client.post(base+'/internal/food-media/upload', content=data,
                headers={'Authorization':'Bearer '+token, 'Content-Type':'image/jpeg'})
        if response.status_code != 200: raise ValueError('upload rejected')
        value = response.json()
        if not re.fullmatch(r'/media/food/[a-f0-9]{64}\.jpg', value['path']) or value['sha256'] != hashlib.sha256(data).hexdigest():
            raise ValueError('invalid upload response')
        return base + value['path']
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise GrokCallFailed('Не удалось разместить фото на OVH. Запись осталась в очереди') from None
