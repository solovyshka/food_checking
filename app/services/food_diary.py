"""Food diary extractor from the user's OpenAI script (meal_type + units).

Uses OpenAI Responses API + JSON schema. Never writes to DB by itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.config import get_settings
from app.services.parser import ParseError

FOOD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "meal_type": {
            "type": ["string", "null"],
            "enum": ["завтрак", "обед", "ужин", "перекус", "напиток", None],
            "description": "Тип приёма пищи.",
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "food": {
                        "type": "string",
                        "description": "Конкретный продукт или основной продукт блюда.",
                    },
                    "dish": {
                        "type": ["string", "null"],
                        "description": "Название блюда, если оно известно.",
                    },
                    "amount": {
                        "type": ["number", "null"],
                        "description": "Количество. Не придумывать, если пользователь его не назвал.",
                    },
                    "unit": {
                        "type": "string",
                        "enum": [
                            "г",
                            "кг",
                            "мл",
                            "л",
                            "шт",
                            "порция",
                            "кусок",
                            "ломтик",
                            "ложка",
                            "чайная ложка",
                            "столовая ложка",
                            "стакан",
                            "тарелка",
                            "не указано",
                        ],
                        "description": "Единица измерения.",
                    },
                    "amount_is_estimate": {
                        "type": "boolean",
                        "description": "true, если количество приблизительное.",
                    },
                },
                "required": [
                    "food",
                    "dish",
                    "amount",
                    "unit",
                    "amount_is_estimate",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["meal_type", "items"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """
Ты — система структурирования дневника питания.

Пользователь диктует на русском языке, что он ел и пил.

Твоя задача:

1. Определить тип приёма пищи.
2. Извлечь продукты.
3. Определить блюда.
4. Извлечь количество и единицы измерения.
5. Определить, является ли количество приблизительным.

------------------------------------------------------------
ТИП ПРИЁМА ПИЩИ
------------------------------------------------------------

В поле meal_type используй только:

- "завтрак"
- "обед"
- "ужин"
- "перекус"
- "напиток"
- null

Определяй meal_type по контексту.

Примеры:

"Сегодня утром съел яйца и овсянку"
-> "завтрак"

"На обед была курица с рисом"
-> "обед"

"Вечером поел макароны"
-> "ужин"

"Днём перекусил яблоком"
-> "перекус"

"Выпил кофе"
-> "напиток"

Если определить тип приёма пищи невозможно:
-> null

НЕ определяй приём пищи только на основании времени,
если пользователь явно не дал достаточно контекста.

------------------------------------------------------------
КОЛИЧЕСТВО
------------------------------------------------------------

Не придумывай количество.

"съел бургер"
-> amount = null

"съел бургер 250 грамм"
-> amount = 250
-> unit = "г"

"примерно 200 грамм курицы"
-> amount = 200
-> unit = "г"
-> amount_is_estimate = true

"два яйца"
-> amount = 2
-> unit = "шт"
-> amount_is_estimate = false

"пару яиц"
-> amount = 2
-> unit = "шт"
-> amount_is_estimate = true

"несколько яблок"
-> amount = null
-> unit = "шт"
-> amount_is_estimate = true

------------------------------------------------------------
ПРИБЛИЗИТЕЛЬНОСТЬ
------------------------------------------------------------

Следующие слова означают приблизительное количество:

"примерно"
"около"
"где-то"
"приблизительно"
"на глаз"
"порядка"
"грамм сто"
"думаю, около"

В этих случаях:
amount_is_estimate = true

Если количество названо точно:
amount_is_estimate = false

------------------------------------------------------------
ЕДИНИЦЫ
------------------------------------------------------------

Нормализуй единицы:

грамм / грамма / граммов -> г
килограмм / килограмма -> кг
миллилитр / миллилитра -> мл
литр / литра -> л
штука / штуки / штук -> шт

------------------------------------------------------------
БЛЮДА
------------------------------------------------------------

Если пользователь говорит:

"съел борщ 400 мл"

создай:

food = "борщ"
dish = "борщ"
amount = 400
unit = "мл"

Если пользователь говорит:

"овсянка с бананом 200 грамм"

можно создать:

food = "овсянка с бананом"
dish = "овсянка с бананом"
amount = 200
unit = "г"

Не придумывай отдельный вес банана.

------------------------------------------------------------
ВАЖНО
------------------------------------------------------------

Не добавляй продукты, которых пользователь не упоминал.

Не рассчитывай калории.

Не рассчитывай БЖУ.

Не угадывай вес.

Не превращай неизвестное количество в приблизительное число.

Если количество неизвестно:

amount = null
unit = "не указано"

Верни результат строго по заданной JSON-схеме.
""".strip()


@dataclass
class FoodDiaryItem:
    food: str
    dish: str | None
    amount: Decimal | None
    unit: str
    amount_is_estimate: bool


@dataclass
class FoodDiaryResult:
    meal_type: str | None
    items: list[FoodDiaryItem]
    raw: dict[str, Any]


def _extract_responses_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("output_text", "text") and part.get("text"):
                chunks.append(str(part["text"]))
    text = "\n".join(chunks).strip()
    if not text:
        raise ParseError("OpenAI Responses API returned empty text")
    return text


def _parse_food_diary(data: dict[str, Any]) -> FoodDiaryResult:
    meal_type = data.get("meal_type")
    if meal_type is not None:
        meal_type = str(meal_type).strip() or None

    items: list[FoodDiaryItem] = []
    raw_items = data.get("items")
    if isinstance(raw_items, list):
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            food = str(raw.get("food") or "").strip()
            if not food:
                continue
            dish_raw = raw.get("dish")
            dish = str(dish_raw).strip() if dish_raw not in (None, "") else None
            amount = None
            if raw.get("amount") is not None and raw.get("amount") != "":
                try:
                    amount = Decimal(str(raw["amount"]))
                except Exception:
                    amount = None
            unit = str(raw.get("unit") or "не указано").strip() or "не указано"
            estimate = bool(raw.get("amount_is_estimate", False))
            items.append(
                FoodDiaryItem(
                    food=food,
                    dish=dish,
                    amount=amount,
                    unit=unit,
                    amount_is_estimate=estimate,
                )
            )

    return FoodDiaryResult(meal_type=meal_type, items=items, raw=data)


def format_food_diary(result: FoodDiaryResult) -> str:
    from app.services.openai_client import provider_label

    meal = result.meal_type or "не указан"
    lines = [f"*Дневник {provider_label()}*", f"Приём пищи: *{meal}*"]
    if not result.items:
        lines.append("_пусто_")
        return "\n".join(lines)

    lines.append("```")
    lines.append(f"{'Еда':<14} {'Кол':>6} {'Ед.':<10} ~?")
    lines.append("-" * 38)
    for item in result.items:
        qty = format(item.amount.normalize(), "f") if item.amount is not None else "—"
        est = "да" if item.amount_is_estimate else "нет"
        label = item.food[:14]
        lines.append(f"{label:<14} {qty:>6} {item.unit[:10]:<10} {est}")
        if item.dish and item.dish != item.food:
            lines.append(f"  dish: {item.dish[:30]}")
    lines.append("```")
    return "\n".join(lines)


async def extract_food_diary(transcript: str) -> FoodDiaryResult:
    """Food diary JSON via OpenAI-compatible API (OpenRouter / OpenAI)."""
    from app.services.openai_client import openai_auth_headers, uses_openai_official

    settings = get_settings()
    if not settings.openai_api_key:
        raise ParseError("OPENAI_API_KEY is not set")

    headers = openai_auth_headers(json_content=True)

    # Official OpenAI: try Responses API first (as in the original script).
    if uses_openai_official():
        payload = {
            "model": settings.openai_parse_model,
            "instructions": SYSTEM_PROMPT,
            "input": transcript,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "food_log",
                    "schema": FOOD_SCHEMA,
                    "strict": True,
                }
            },
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/responses",
                headers=headers,
                json=payload,
            )
        if response.status_code == 200:
            try:
                data = json.loads(_extract_responses_text(response.json()))
            except (json.JSONDecodeError, ParseError) as exc:
                raise ParseError(f"Bad food diary JSON: {exc}") from exc
            return _parse_food_diary(data)

    # OpenRouter and fallbacks: chat.completions (+ structured outputs when possible).
    return await _extract_via_chat(transcript, headers)


async def _extract_via_chat(transcript: str, headers: dict[str, str]) -> FoodDiaryResult:
    settings = get_settings()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Разложи текст в JSON по схеме food_log "
                "(meal_type, items[food,dish,amount,unit,amount_is_estimate]):\n\n"
                f"{transcript}"
            ),
        },
    ]
    # Prefer strict json_schema (OpenRouter structured outputs); fall back to json_object.
    attempts = [
        {
            "type": "json_schema",
            "json_schema": {
                "name": "food_log",
                "strict": True,
                "schema": FOOD_SCHEMA,
            },
        },
        {"type": "json_object"},
    ]
    last_error = ""
    async with httpx.AsyncClient(timeout=180.0) as client:
        for response_format in attempts:
            payload = {
                "model": settings.openai_parse_model,
                "temperature": 0,
                "response_format": response_format,
                "messages": messages,
            }
            response = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            if response.status_code != 200:
                last_error = f"{response.status_code}: {response.text[:400]}"
                continue
            content = (
                response.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content")
                or ""
            )
            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                last_error = f"Bad JSON: {exc}"
                continue
            return _parse_food_diary(data)

    raise ParseError(f"Food diary API error: {last_error}")
