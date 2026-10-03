"""Authenticated single-owner API for the Android diary, separate from bot API."""
import hashlib
import json
import logging
import os
import secrets
import time
from collections import deque
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ConsumptionEntry, Product
from app.db.mobile_models import MobileEntryDetails, MobileSubmission
from app.db.session import get_db
from app.services.inventory import normalize_name
from app.services.mobile_parser import parse_consumption_text
from app.services.transcription import transcribe_for_pipeline

log = logging.getLogger(__name__)
app = FastAPI(title="Food mobile", docs_url=None, redoc_url=None, openapi_url=None)
Meal = Literal["breakfast", "lunch", "dinner", "snack"]
NutritionSource = Literal["estimate", "label", "manual"]
attempts: deque[float] = deque()


def token() -> str:
    value = os.environ.get("FOOD_MOBILE_TOKEN", "")
    if len(value) < 32:
        raise HTTPException(503, "Подключение приложения пока не настроено")
    return value


def authorize(authorization: Annotated[str | None, Header()] = None):
    expected = "Bearer " + token()
    if not secrets.compare_digest((authorization or "").encode(), expected.encode()):
        raise HTTPException(401, "Откройте настройки и подключитесь к коробке")


class Pair(BaseModel):
    code: str = Field(min_length=6, max_length=32)


@app.post("/api/mobile/pair")
def pair(body: Pair):
    # A global limit also prevents an attacker bypassing it through proxy IPs.
    now = time.monotonic()
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 5:
        raise HTTPException(429, "Слишком много попыток. Подождите минуту")
    attempts.append(now)
    configured = os.environ.get("FOOD_MOBILE_PAIR_CODE", "")
    if not configured or not secrets.compare_digest(body.code.encode(), configured.encode()):
        raise HTTPException(401, "Неверный код подключения")
    return {"token": token()}


Db = Annotated[Session, Depends(get_db)]
Auth = Depends(authorize)


@app.get("/api/mobile/health", dependencies=[Auth])
def health():
    return {"status": "ok", "version": "1.0.0"}


class Item(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(gt=0, le=100000, max_digits=9, decimal_places=3)
    unit: Literal["г", "мл"]
    kcal_per_100g: Decimal | None = Field(default=None, ge=0, le=2000, max_digits=6, decimal_places=2)
    nutrition_source: NutritionSource = "estimate"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Укажите название")
        return value


class Preview(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@app.post("/api/mobile/preview", dependencies=[Auth])
async def preview(body: Preview, db: Db):
    try:
        parsed, _ = await parse_consumption_text(body.text)
        items = []
        for item in parsed.items[:30]:
            source = "estimate"
            kcal = item.kcal_per_100g
            unit = item.unit if item.unit in ("г", "мл") else "г"
            # Reuse only an explicitly verified label for this exact product and unit.
            known = db.execute(select(ConsumptionEntry.kcal_per_100g)
                .join(Product).join(MobileEntryDetails, MobileEntryDetails.entry_id == ConsumptionEntry.id)
                .where(Product.name_normalized == normalize_name(item.name), ConsumptionEntry.unit == unit,
                       ConsumptionEntry.status == "confirmed", MobileEntryDetails.nutrition_source == "label",
                       ConsumptionEntry.kcal_per_100g.is_not(None))
                .order_by(ConsumptionEntry.id.desc()).limit(1)).scalar_one_or_none()
            if known is not None:
                kcal, source = known, "label"
            items.append({"name": item.name, "quantity": str(item.quantity) if item.quantity else "",
                          "unit": unit, "kcal_per_100g": str(kcal) if kcal is not None else "",
                          "nutrition_source": source})
        if not items:
            raise HTTPException(422, "Не удалось выделить продукты. Уточните текст или добавьте вручную")
        return {"items": items, "skipped": parsed.skipped}
    except HTTPException:
        raise
    except Exception:
        log.exception("Mobile food parsing failed")
        raise HTTPException(502, "Расчёт сейчас недоступен. Текст сохранён, попробуйте ещё раз") from None


@app.post("/api/mobile/transcribe", dependencies=[Auth])
async def transcribe(file: UploadFile = File(...)):
    data = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    if not data or len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Запись пуста или слишком большая")
    try:
        text, backend, _ = await transcribe_for_pipeline(data, filename="voice.m4a")
        return {"text": text, "backend": backend}
    except Exception:
        log.exception("Mobile transcription failed")
        raise HTTPException(502, "Не удалось распознать голос. Повторите запись или введите текст") from None


class Save(BaseModel):
    request_id: UUID
    entry_date: date
    meal: Meal
    text: str = Field(default="", max_length=8000)
    items: list[Item] = Field(min_length=1, max_length=30)


def serialize(entry: ConsumptionEntry, details: MobileEntryDetails | None):
    kcal = None
    if entry.kcal_per_100g is not None and entry.unit in ("г", "мл"):
        kcal = (entry.quantity * entry.kcal_per_100g / 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return {"id": entry.id, "name": entry.product.name, "quantity": str(entry.quantity),
            "unit": entry.unit, "kcal_per_100g": str(entry.kcal_per_100g) if entry.kcal_per_100g is not None else None,
            "kcal": str(kcal) if kcal is not None else None, "meal": details.meal if details else "snack",
            "nutrition_source": details.nutrition_source if details else "unknown"}


@app.get("/api/mobile/diary", dependencies=[Auth])
def diary(entry_date: date, db: Db):
    rows = db.execute(select(ConsumptionEntry, MobileEntryDetails)
        .outerjoin(MobileEntryDetails, MobileEntryDetails.entry_id == ConsumptionEntry.id)
        .where(ConsumptionEntry.entry_date == entry_date, ConsumptionEntry.status == "confirmed")
        .order_by(ConsumptionEntry.id)).all()
    items = [serialize(entry, details) for entry, details in rows]
    total = sum((Decimal(i["kcal"]) for i in items if i["kcal"] is not None), Decimal(0))
    return {"entry_date": entry_date.isoformat(), "items": items, "total_kcal": str(total),
            "missing_kcal": sum(i["kcal"] is None for i in items)}


def product_for(db, item):
    normalized = normalize_name(item.name)
    product = db.execute(select(Product).where(Product.name_normalized == normalized)).scalar_one_or_none()
    if product is None:
        product = Product(name=item.name, name_normalized=normalized, unit=item.unit)
        db.add(product)
        db.flush()
    return product


@app.post("/api/mobile/entries", dependencies=[Auth])
def save(body: Save, db: Db):
    request_id = str(body.request_id)
    digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def previous():
        prior = db.get(MobileSubmission, request_id)
        if prior:
            if prior.payload_hash != digest:
                raise HTTPException(409, "Этот запрос уже сохранён с другими данными")
            return prior.response
        return None

    if (result := previous()) is not None:
        return result
    try:
        now = datetime.now(get_settings().timezone)
        ids = []
        for item in body.items:
            entry = ConsumptionEntry(product_id=product_for(db, item).id, quantity=item.quantity,
                unit=item.unit, kcal_per_100g=item.kcal_per_100g, status="confirmed", source="mobile",
                transcript=body.text, batch_id=request_id, entry_date=body.entry_date,
                recorded_at=now, confirmed_at=now)
            db.add(entry)
            db.flush()
            ids.append(entry.id)
            db.add(MobileEntryDetails(entry_id=entry.id, meal=body.meal, nutrition_source=item.nutrition_source))
        result = {"ids": ids}
        db.add(MobileSubmission(id=request_id, payload_hash=digest, response=result))
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        if (result := previous()) is not None:
            return result
        raise HTTPException(409, "Запись обновлялась одновременно. Повторите сохранение") from None


class Edit(Item):
    meal: Meal


def active_entry(db, entry_id):
    entry = db.get(ConsumptionEntry, entry_id)
    if entry is None or entry.status != "confirmed":
        raise HTTPException(404, "Запись не найдена")
    return entry


@app.patch("/api/mobile/entries/{entry_id}", dependencies=[Auth])
def edit(entry_id: int, body: Edit, db: Db):
    entry = active_entry(db, entry_id)
    entry.product_id = product_for(db, body).id
    entry.quantity, entry.unit, entry.kcal_per_100g = body.quantity, body.unit, body.kcal_per_100g
    details = db.get(MobileEntryDetails, entry_id)
    if details is None:
        details = MobileEntryDetails(entry_id=entry_id)
        db.add(details)
    details.meal, details.nutrition_source = body.meal, body.nutrition_source
    db.commit()
    return {"status": "ok"}


@app.delete("/api/mobile/entries/{entry_id}", dependencies=[Auth])
def delete(entry_id: int, db: Db):
    entry = active_entry(db, entry_id)
    entry.status = "cancelled"
    db.commit()
    return {"status": "ok"}
