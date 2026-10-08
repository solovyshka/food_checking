"""Macronutrients per 100 g/ml and deterministic totals for a portion."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, Field

MACRO_FIELDS = ("protein_per_100g", "fat_per_100g", "carbs_per_100g")


class MacroValues(BaseModel):
    protein_per_100g: Decimal | None = Field(default=None, ge=0, le=200, max_digits=5, decimal_places=2)
    fat_per_100g: Decimal | None = Field(default=None, ge=0, le=200, max_digits=5, decimal_places=2)
    carbs_per_100g: Decimal | None = Field(default=None, ge=0, le=200, max_digits=5, decimal_places=2)
    macros_source: Literal["estimate", "label", "manual"] = "estimate"


def serialized_macros(value, quantity):
    result = {"macros_source": value.macros_source if value else "estimate"}
    for field, total_field in zip(MACRO_FIELDS, ("protein", "fat", "carbs")):
        per_100 = getattr(value, field, None)
        result[field] = str(per_100) if per_100 is not None else None
        total = None if per_100 is None or quantity is None else (
            per_100 * quantity / 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        result[total_field] = str(total) if total is not None else None
    return result


def daily_macros(rows):
    """Round daily totals once; unknown portions/nutrients never become zero."""
    result = {"missing_macros": {}, "estimated_macros": {}}
    for field, nutrient in zip(MACRO_FIELDS, ("protein", "fat", "carbs")):
        values = []
        estimated = False
        for row in rows:
            known = row.get(field) is not None and row.get("quantity") is not None and row.get("unit") in ("г", "мл")
            if known:
                values.append(Decimal(row[field]) * Decimal(row["quantity"]) / 100)
                estimated |= row.get("macros_source") == "estimate" or row.get("portion_is_estimate") is True
        total = sum(values, Decimal(0)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        result["total_" + nutrient] = str(total) if values or not rows else None
        result["missing_macros"][nutrient] = len(rows) - len(values)
        result["estimated_macros"][nutrient] = estimated
    return result


def update_macros(value, body, *, same_product):
    """Old clients may edit a portion without sending nutrient fields."""
    for field in MACRO_FIELDS:
        if field in body.model_fields_set:
            setattr(value, field, getattr(body, field))
        elif not same_product:
            setattr(value, field, None)
    if "macros_source" in body.model_fields_set:
        value.macros_source = body.macros_source
    elif not same_product or any(field in body.model_fields_set for field in MACRO_FIELDS):
        value.macros_source = "manual"
