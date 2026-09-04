import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from app.config import get_settings

SYSTEM_PROMPT = """Ты извлекаешь список продуктов из русского текста.
Единицы измерения НЕ указывай — их задаёт справочник отдельно.
Верни ТОЛЬКО валидный JSON без markdown:
{
  "entry_date": "YYYY-MM-DD" или null,
  "items": [
    {"name": "молоко", "quantity": 2}
  ]
}
name — короткое каноническое название продукта (молоко, гречка, сыр, колбаса, зелень).
quantity — сколько единиц назвал человек (число). Слова вроде литр/пачка/кг игнорируй как единицу, но число оставь.
Если количество неясно — всё равно верни item с "quantity": null.
Если дата не указана явно, entry_date = null."""


@dataclass
class ParsedItem:
    name: str
    quantity: Decimal | None


@dataclass
class ParsedInventory:
    entry_date: date | None
    items: list[ParsedItem]
    skipped: list[str]


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
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _parse_quantity(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        qty = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, TypeError, AttributeError):
        return None
    if qty <= 0:
        return None
    return qty


def _parse_items(raw_items: object) -> tuple[list[ParsedItem], list[str]]:
    items: list[ParsedItem] = []
    skipped: list[str] = []
    if not isinstance(raw_items, list) or not raw_items:
        return items, skipped
    for raw in raw_items:
        if not isinstance(raw, dict):
            skipped.append(str(raw))
            continue
        name = str(raw.get("name", "")).strip()
        if not name:
            skipped.append("позиция без названия")
            continue
        quantity = _parse_quantity(raw.get("quantity"))
        items.append(ParsedItem(name=name, quantity=quantity))
    return items, skipped


def parsed_from_model_content(content: str) -> ParsedInventory:
    try:
        data = _extract_json(content)
    except ParseError:
        return ParsedInventory(
            entry_date=None,
            items=[],
            skipped=["не удалось разобрать ответ модели"],
        )
    items, skipped = _parse_items(data.get("items"))
    return ParsedInventory(
        entry_date=_parse_entry_date(data.get("entry_date")),
        items=items,
        skipped=skipped,
    )


async def parse_inventory_text(transcript: str) -> ParsedInventory:
    """Local Ollama / Qwen parser."""
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
    return parsed_from_model_content(content)


async def parse_inventory_text_openai(transcript: str) -> ParsedInventory:
    """Cloud chat parser via OpenAI-compatible API (same JSON as local)."""
    from app.services.openai_client import openai_auth_headers

    settings = get_settings()
    if not settings.openai_api_key:
        raise ParseError("OPENAI_API_KEY is not set")
    payload = {
        "model": settings.openai_parse_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    }
    headers = openai_auth_headers(json_content=True)
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )
    if response.status_code != 200:
        raise ParseError(f"Cloud parse error {response.status_code}: {response.text}")
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    return parsed_from_model_content(content or "")
