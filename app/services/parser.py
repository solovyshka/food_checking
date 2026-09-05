import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from app.catalog.units import CONSUMPTION_UNITS
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

CONSUMPTION_SYSTEM_PROMPT = """Ты извлекаешь съеденное из русского текста.
Справочника продуктов нет: записывай ВСЕ блюда и продукты, как их назвали (борщ, омлет, яблоко, кофе).
Не подгоняй названия под складской список (не «пачка», не «десяток»).
Единицы ТОЛЬКО "г" или "мл". Переведи порции, штуки, стаканы, тарелки, кг, литры в граммы или миллилитры (оценка нормальна).
Жидкости (напитки, суп, молоко, сок) — мл. Всё остальное — г.
Примеры порций: 2 яйца → 120 г; стакан молока → 200 мл; тарелка супа → 250 мл; кусок хлеба → 30 г; 0.5 кг курицы → 500 г.

kcal_per_100g — опционально. Заполняй ТОЛЬКО если человек ЯВНО назвал калорийность.
Не выдумывай калории из своих знаний, если не сказано — ставь null.
Человек говорит просто «калорийность N калорий/ккал», БЕЗ «на 100 грамм». Это число и пиши в kcal_per_100g как есть, не пересчитывай на порцию.
Пример: «съел 100 грамм творога 2% с калорийностью 100 калорий» → quantity 100, unit "г", kcal_per_100g 100.
«борщ тарелка» без калорийности → kcal_per_100g null.

Верни ТОЛЬКО валидный JSON без markdown:
{
  "entry_date": "YYYY-MM-DD" или null,
  "items": [
    {"name": "творог", "quantity": 150, "unit": "г", "kcal_per_100g": 110},
    {"name": "борщ", "quantity": 250, "unit": "мл", "kcal_per_100g": null}
  ]
}
name — как сказано, коротко.
quantity — число уже в г или мл.
Всегда оцени quantity, даже если порция «примерно» или «тарелка».
Если дата не указана явно, entry_date = null."""

_UNIT_ALIASES = {
    "г": "г",
    "гр": "г",
    "грамм": "г",
    "грамма": "г",
    "граммов": "г",
    "g": "г",
    "мл": "мл",
    "миллилитр": "мл",
    "миллилитра": "мл",
    "миллилитров": "мл",
    "ml": "мл",
    "кг": "кг",
    "килограмм": "кг",
    "килограмма": "кг",
    "kg": "кг",
    "л": "л",
    "литр": "л",
    "литра": "л",
    "литров": "л",
    "l": "л",
}


@dataclass
class ParsedItem:
    name: str
    quantity: Decimal | None
    unit: str | None = None
    kcal_per_100g: Decimal | None = None


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


def _parse_kcal_per_100g(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        kcal = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, TypeError, AttributeError):
        return None
    if kcal < 0 or kcal > 2000:
        return None
    return kcal


def _normalize_consumption_qty_unit(
    quantity: Decimal | None, unit_raw: object
) -> tuple[Decimal | None, str | None]:
    alias = str(unit_raw or "").strip().lower()
    unit = _UNIT_ALIASES.get(alias)
    if unit == "кг":
        if quantity is not None:
            quantity = quantity * Decimal(1000)
        return quantity, "г"
    if unit == "л":
        if quantity is not None:
            quantity = quantity * Decimal(1000)
        return quantity, "мл"
    if unit in CONSUMPTION_UNITS:
        return quantity, unit
    return quantity, None


def _parse_items(
    raw_items: object, *, consumption: bool = False
) -> tuple[list[ParsedItem], list[str]]:
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
        unit: str | None = None
        kcal_per_100g: Decimal | None = None
        if consumption:
            quantity, unit = _normalize_consumption_qty_unit(quantity, raw.get("unit"))
            if unit is None:
                skipped.append(f"{name}: единица не г/мл")
                continue
            kcal_per_100g = _parse_kcal_per_100g(
                raw.get("kcal_per_100g", raw.get("calories_per_100g", raw.get("kcal", raw.get("calories"))))
            )
        items.append(
            ParsedItem(
                name=name,
                quantity=quantity,
                unit=unit,
                kcal_per_100g=kcal_per_100g,
            )
        )
    return items, skipped


def parsed_from_model_content(
    content: str, *, consumption: bool = False
) -> ParsedInventory:
    try:
        data = _extract_json(content)
    except ParseError:
        return ParsedInventory(
            entry_date=None,
            items=[],
            skipped=["не удалось разобрать ответ модели"],
        )
    items, skipped = _parse_items(data.get("items"), consumption=consumption)
    return ParsedInventory(
        entry_date=_parse_entry_date(data.get("entry_date")),
        items=items,
        skipped=skipped,
    )


async def _parse_with_ollama(transcript: str, system_prompt: str) -> str:
    settings = get_settings()
    payload = {
        "model": settings.qwen_model,
        "stream": False,
        "keep_alive": settings.ollama_keep_alive,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
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
    return response.json().get("message", {}).get("content", "") or ""


async def parse_inventory_text(transcript: str) -> ParsedInventory:
    """Local Ollama / Qwen parser for stock (catalog units)."""
    content = await _parse_with_ollama(transcript, SYSTEM_PROMPT)
    return parsed_from_model_content(content)


async def parse_consumption_text(transcript: str) -> ParsedInventory:
    """Local parser for eaten food: any name, units г/мл."""
    content = await _parse_with_ollama(transcript, CONSUMPTION_SYSTEM_PROMPT)
    return parsed_from_model_content(content, consumption=True)


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
