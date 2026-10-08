"""Factory GTINs and Open Food Facts lookup; never infer nutrition from a code."""
import re
import time
from collections import deque
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import httpx

from app.services.grok_bot import proxy_url

OFF_FIELDS = "code,product_name,product_name_ru,brands,nutriments,product_quantity_unit,serving_quantity_unit"
OFF_REQUESTS: deque[float] = deque()
OFF_CREDIT = {"name": "Open Food Facts", "url": "https://world.openfoodfacts.org",
              "license": "ODbL", "license_url": "https://opendatacommons.org/licenses/odbl/1-0/"}


def normalize_barcode(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})", value):
        raise ValueError("Нужен товарный штрихкод из 8, 12, 13 или 14 цифр")
    total = sum(int(digit) * (3 if index % 2 == 0 else 1)
                for index, digit in enumerate(reversed(value[:-1])))
    if (10 - total % 10) % 10 != int(value[-1]):
        raise ValueError("Неверная контрольная цифра штрихкода. Проверьте номер")
    result = value.zfill(14)
    # Retailer/internal numbering and variable-measure GTINs are out of scope.
    if (result.startswith("02") or result.startswith("002") or
            result.startswith("0000002") or result.startswith("9")):
        raise ValueError("Поддерживаем только фабричные товары. Код магазина или весового товара не подходит")
    return result


def nutrition_value(value, maximum):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= maximum:
            return None
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


class BarcodeLookupUnavailable(RuntimeError):
    pass


async def lookup_openfoodfacts(code: str):
    now = time.monotonic()
    while OFF_REQUESTS and OFF_REQUESTS[0] <= now - 60:
        OFF_REQUESTS.popleft()
    if len(OFF_REQUESTS) >= 14:
        raise BarcodeLookupUnavailable("Поиск временно ограничен. Повторите через минуту или введите данные с упаковки")
    OFF_REQUESTS.append(now)
    try:
        async with httpx.AsyncClient(proxy=proxy_url(), timeout=10, follow_redirects=False) as client:
            response = await client.get(f"https://world.openfoodfacts.org/api/v2/product/{code}.json",
                params={"fields": OFF_FIELDS}, headers={
                    "User-Agent": "FoodChecking/1.0 (https://github.com/solovyshka/food_checking)"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == 0:
            return None
        product = payload.get("product")
        if not isinstance(product, dict) or normalize_barcode(str(product.get("code", ""))) != normalize_barcode(code):
            raise ValueError("Wrong product code")
        name = (product.get("product_name_ru") or product.get("product_name") or "").strip()
        if not name:
            return None
        brand = (product.get("brands") or "").strip()
        if brand and brand.casefold() not in name.casefold():
            name = f"{brand} — {name}"
        values = product.get("nutriments") or {}
        kcal = nutrition_value(values.get("energy-kcal_100g"), 2000)
        if kcal is None:
            kj = nutrition_value(values.get("energy-kj_100g"), 8368)
            if kj is not None:
                kcal = nutrition_value(Decimal(kj) / Decimal("4.184"), 2000)
        units = (product.get("product_quantity_unit"), product.get("serving_quantity_unit"))
        return {"name": name[:255], "unit": "мл" if any(unit in ("ml", "cl", "l") for unit in units) else "г",
                "kcal_per_100g": kcal, "protein_per_100g": nutrition_value(values.get("proteins_100g"), 200),
                "fat_per_100g": nutrition_value(values.get("fat_100g"), 200),
                "carbs_per_100g": nutrition_value(values.get("carbohydrates_100g"), 200),
                "source": "openfoodfacts", "verified": False,
                "source_url": f"https://world.openfoodfacts.org/product/{code}"}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise BarcodeLookupUnavailable("Open Food Facts сейчас недоступен. Можно ввести данные с упаковки") from None
