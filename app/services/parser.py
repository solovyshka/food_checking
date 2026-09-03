import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from app.config import get_settings

SYSTEM_PROMPT = """Ты извлекаешь список продуктов из русского текста.
Верни ТОЛЬКО валидный JSON без markdown:
{
  "entry_date": "YYYY-MM-DD" или null,
  "items": [
    {"name": "картофель", "quantity": 2, "unit": "кг"}
  ]
}
Если дата не указана явно, entry_date = null.
quantity — число. unit — кг, л, шт, пачка и т.п."""


@dataclass
class ParsedItem:
    name: str
    quantity: Decimal
    unit: str


@dataclass
class ParsedInventory:
    entry_date: date | None
    items: list[ParsedItem]


class ParseError(Exception):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ParseError("Model did not return JSON") from None
        return json.loads(match.group(0))


def _parse_entry_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value.strip())
    return None


def _parse_items(raw_items: object) -> list[ParsedItem]:
    if not isinstance(raw_items, list) or not raw_items:
        raise ParseError("No products found in transcript")
    items: list[ParsedItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        unit = str(raw.get("unit", "шт")).strip() or "шт"
        if not name:
            continue
        try:
            quantity = Decimal(str(raw.get("quantity", 1)))
        except (InvalidOperation, TypeError) as exc:
            raise ParseError(f"Invalid quantity for {name}") from exc
        items.append(ParsedItem(name=name, quantity=quantity, unit=unit))
    if not items:
        raise ParseError("No valid products parsed")
    return items


async def parse_inventory_text(transcript: str) -> ParsedInventory:
    settings = get_settings()
    payload = {
        "model": settings.qwen_model,
        "stream": False,
        "keep_alive": settings.ollama_keep_alive,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            f"{settings.qwen_url.rstrip('/')}/api/chat",
            json=payload,
        )
    if response.status_code != 200:
        raise ParseError(f"Ollama error {response.status_code}: {response.text}")
    content = response.json().get("message", {}).get("content", "")
    data = _extract_json(content)
    return ParsedInventory(
        entry_date=_parse_entry_date(data.get("entry_date")),
        items=_parse_items(data.get("items")),
    )
