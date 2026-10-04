"""Authenticated single-owner API for the Android diary, separate from bot API."""
import hashlib
import json
import logging
import os
import secrets
import time
from collections import deque
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db.models import ConsumptionEntry, ConsumptionTranscript, Product
from app.db.mobile_models import (MobileEntryDetails, MobileSubmission, MobileImage, MobileTranscriptDetails,
                                  MobileParseJob, MobileParsedFood, MobileParsedNutrition)
from app.db.session import get_db
from app.services.grok_bot import (GrokCallFailed, GrokNotConfigured, GrokDispatchUnknown,
                                   send_test, send_analysis, check_analysis_config)
from app.services.grok_analysis import (AnalysisResult, apply_result, authorize_job, digest_json, expire_job,
    fail_job, job_input, normalized_image, now_utc, serialized_job, serialized_rows, webhook_payload)
from app.services.food_images import publish_image
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


class GrokTest(BaseModel):
    text: str = Field(default="", max_length=2000)
    has_image: bool = False


@app.post("/api/mobile/grok/test", dependencies=[Auth])
async def grok_test(body: GrokTest):
    try:
        await send_test(body.text, has_image=body.has_image)
    except GrokNotConfigured as exc:
        raise HTTPException(503, str(exc)) from None
    except GrokCallFailed as exc:
        raise HTTPException(502, str(exc)) from None
    return {
        "accepted": True,
        "detail": "Grok принял тестовое сообщение. Ответ появится в чате бота.",
    }


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


MEAL_NAMES = {"breakfast": "завтрак", "lunch": "обед", "dinner": "ужин", "snack": "перекус"}


class QueueText(BaseModel):
    request_id: UUID
    entry_date: date
    meal: Meal
    text: str = Field(default="", max_length=8000)
    image_id: UUID | None = None

    @field_validator("text")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        return value

    @model_validator(mode="after")
    def text_or_photo(self):
        if not self.text and self.image_id is None:
            raise ValueError("Напишите, что съели, или добавьте картинку")
        return self


@app.post("/api/mobile/images", dependencies=[Auth])
async def upload_image(db: Db, file: UploadFile = File(...)):
    data = await file.read(4 * 1024 * 1024 + 1)
    await file.close()
    if not data or len(data) > 4 * 1024 * 1024:
        raise HTTPException(413, "Выберите картинку размером до 4 МБ")
    data = await run_in_threadpool(normalized_image, data)
    digest = hashlib.sha256(data).hexdigest()
    def previous():
        return db.scalars(select(MobileImage).where(MobileImage.sha256 == digest)).one_or_none()
    if (existing := previous()) is not None:
        return {"id": existing.id}
    image = MobileImage(id=str(uuid4()), sha256=digest, data=data)
    try:
        db.add(image)
        db.commit()
    except IntegrityError:
        db.rollback()
        image = previous()
        if image is None:
            raise HTTPException(409, "Повторите загрузку картинки") from None
    return {"id": image.id}


@app.post("/api/mobile/queue", dependencies=[Auth])
def enqueue(body: QueueText, db: Db):
    request_id = str(body.request_id)
    payload = {"kind": "queue", **body.model_dump(mode="json", exclude_none=True)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def previous():
        prior = db.get(MobileSubmission, request_id)
        if prior is None:
            return None
        if prior.payload_hash != digest:
            raise HTTPException(409, "Этот запрос уже сохранён с другими данными")
        return prior.response

    if (result := previous()) is not None:
        return result
    try:
        if body.image_id is not None and db.get(MobileImage, str(body.image_id)) is None:
            raise HTTPException(422, "Сначала загрузите картинку")
        now = datetime.now(get_settings().timezone)
        entry = ConsumptionTranscript(text=body.text, status="queued", source="mobile",
            meal_type=MEAL_NAMES[body.meal], entry_date=body.entry_date,
            recorded_at=now, confirmed_at=now)
        db.add(entry)
        db.flush()
        if body.image_id is not None:
            db.add(MobileTranscriptDetails(transcript_id=entry.id, image_id=str(body.image_id)))
        result = {"id": entry.id, "status": "queued"}
        db.add(MobileSubmission(id=request_id, payload_hash=digest, response=result))
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        if (result := previous()) is not None:
            return result
        raise HTTPException(409, "Запись обновлялась одновременно. Повторите сохранение") from None


@app.get("/api/mobile/queue", dependencies=[Auth])
def queued_texts(db: Db):
    for job in db.scalars(select(MobileParseJob).where(MobileParseJob.status.in_(("dispatching", "running", "dispatch_unknown")))):
        expire_job(db, job)
    rows = db.scalars(select(ConsumptionTranscript)
        .where(ConsumptionTranscript.source == "mobile", ConsumptionTranscript.status == "queued")
        .order_by(ConsumptionTranscript.entry_date, ConsumptionTranscript.recorded_at, ConsumptionTranscript.id)).all()
    photos = dict(db.execute(select(MobileTranscriptDetails.transcript_id, MobileTranscriptDetails.image_id)).all())
    jobs = db.scalars(select(MobileParseJob).order_by(MobileParseJob.created_at.desc()).limit(20)).all()
    return {"items": [{"id": row.id, "text": row.text, "entry_date": row.entry_date.isoformat(),
                      "meal": next((key for key, name in MEAL_NAMES.items() if name == row.meal_type), "snack"),
                      "has_image": row.id in photos, "job_id": row.parse_batch_id,
                      "status": "processing" if row.parse_batch_id else "queued"} for row in rows],
            "jobs": [{"id": job.id, "status": job.status, "error": job.error} for job in jobs]}


class StartAnalysis(BaseModel):
    request_id: UUID
    entry_ids: list[int] = Field(min_length=1, max_length=30)

    @field_validator("entry_ids")
    @classmethod
    def unique_ids(cls, ids):
        if any(value <= 0 for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Выберите разные записи очереди")
        return sorted(ids)


@app.post("/api/mobile/queue/parse", dependencies=[Auth])
async def start_analysis(body: StartAnalysis, db: Db):
    job_id = str(body.request_id)
    digest = digest_json(body.model_dump(mode="json"))
    existing = db.get(MobileParseJob, job_id)
    if existing is not None:
        if existing.request_hash != digest:
            raise HTTPException(409, "Этот запрос уже использован для другого разбора")
        return serialized_job(db, existing)
    try:
        check_analysis_config()
    except GrokNotConfigured as exc:
        raise HTTPException(503, str(exc)) from None
    rows = db.scalars(select(ConsumptionTranscript).where(ConsumptionTranscript.id.in_(body.entry_ids))
        .order_by(ConsumptionTranscript.id).with_for_update()).all()
    if len(rows) != len(body.entry_ids) or any(row.source != "mobile" or row.status != "queued" or row.parse_batch_id for row in rows):
        raise HTTPException(409, "Некоторые записи уже разобраны или переданы на разбор. Обновите очередь")
    if sum(len(row.text) for row in rows) > 80000:
        raise HTTPException(422, "Слишком много текста для одного разбора. Выберите меньше записей")
    photos = dict(db.execute(select(MobileTranscriptDetails.transcript_id, MobileTranscriptDetails.image_id)
        .where(MobileTranscriptDetails.transcript_id.in_(body.entry_ids))).all())
    inputs = [{"transcript_id": row.id, "entry_date": row.entry_date.isoformat(),
               "meal": row.meal_type, "text": row.text, "image_id": photos.get(row.id)} for row in rows]
    job = MobileParseJob(id=job_id, request_hash=digest, nonce=str(uuid4()), status="dispatching", inputs=inputs,
        expires_at=now_utc() + timedelta(hours=1))
    try:
        db.add(job)
        for row in rows:
            row.parse_batch_id = job_id
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(MobileParseJob, job_id)
        if existing is not None and existing.request_hash == digest:
            return serialized_job(db, existing)
        raise HTTPException(409, "Очередь обновлялась одновременно. Повторите запрос") from None
    status, error = "running", None
    try:
        hosted = {}
        inputs_with_links = []
        for entry in job.inputs:
            value = dict(entry)
            if entry.get("image_id"):
                image = db.get(MobileImage, entry["image_id"])
                if image is None or not image.data:
                    raise GrokCallFailed("Фото не найдено. Запись осталась в очереди")
                if image.id not in hosted:
                    hosted[image.id] = await publish_image(image.data)
                value["image_url"] = hosted[image.id]
            inputs_with_links.append(value)
        job.inputs = inputs_with_links
        db.commit()
        await send_analysis(webhook_payload(job))
    except GrokDispatchUnknown as exc:
        status, error = "dispatch_unknown", str(exc)
    except (GrokCallFailed, GrokNotConfigured) as exc:
        status, error = "failed", str(exc)
    db.refresh(job, with_for_update=True)
    if job.status == "dispatching":
        if status == "failed":
            fail_job(db, job, error)
        else:
            job.status, job.error = status, error
            db.commit()
    return serialized_job(db, job)


@app.get("/api/mobile/queue/jobs/{job_id}", dependencies=[Auth])
def analysis_status(job_id: UUID, db: Db):
    job = db.get(MobileParseJob, str(job_id))
    if job is None:
        raise HTTPException(404, "Разбор не найден")
    return serialized_job(db, job)


@app.get("/api/mobile/grok/jobs/{job_id}/input")
def analysis_input(job_id: UUID, db: Db, authorization: Annotated[str | None, Header()] = None):
    return job_input(db, authorize_job(db, job_id, authorization))


@app.get("/api/mobile/grok/jobs/{job_id}/images/{image_id}")
def analysis_image(job_id: UUID, image_id: UUID, db: Db, authorization: Annotated[str | None, Header()] = None):
    job = authorize_job(db, job_id, authorization)
    if str(image_id) not in {entry.get("image_id") for entry in job.inputs}:
        raise HTTPException(404, "Картинка не относится к этой задаче")
    image = db.get(MobileImage, str(image_id))
    if image is None:
        raise HTTPException(404, "Картинка не найдена")
    return Response(image.data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/mobile/grok/jobs/{job_id}/result")
def analysis_result(job_id: UUID, body: AnalysisResult, db: Db, authorization: Annotated[str | None, Header()] = None):
    job = authorize_job(db, job_id, authorization)
    db.refresh(job, with_for_update=True)
    apply_result(db, job, body)
    return {"saved": True, "status": job.status}


@app.delete("/api/mobile/queue/{entry_id}", dependencies=[Auth])
def remove_queued_text(entry_id: int, db: Db):
    entry = db.scalars(select(ConsumptionTranscript)
        .where(ConsumptionTranscript.id == entry_id, ConsumptionTranscript.source == "mobile")
        .with_for_update()).one_or_none()
    if entry is None or entry.status not in ("queued", "cancelled"):
        raise HTTPException(404, "Запись очереди не найдена")
    if entry.parse_batch_id:
        raise HTTPException(409, "Запись уже передана на разбор")
    entry.status = "cancelled"
    db.commit()
    return {"cancelled": True}


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
    tables = serialized_rows(db, entry_date=entry_date)
    total += sum((Decimal(item["kcal"]) for item in tables["nutrition"] if item["kcal"] is not None), Decimal(0))
    queued_count = db.scalar(select(func.count()).select_from(ConsumptionTranscript)
        .where(ConsumptionTranscript.source == "mobile", ConsumptionTranscript.status == "queued",
               ConsumptionTranscript.entry_date == entry_date))
    return {"entry_date": entry_date.isoformat(), "items": items, "total_kcal": str(total),
            "missing_kcal": sum(i["kcal"] is None for i in items) + sum(i["kcal"] is None for i in tables["nutrition"]),
            "queued_count": queued_count, **tables}


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


def active_food(db, food_id):
    food = db.scalars(select(MobileParsedFood).where(MobileParsedFood.id == food_id)
                      .with_for_update()).one_or_none()
    if food is None or not food.active:
        raise HTTPException(404, "Продукт не найден")
    return food


@app.patch("/api/mobile/grok/foods/{food_id}", dependencies=[Auth])
def edit_food(food_id: int, body: Item, db: Db):
    food = active_food(db, food_id)
    nutrition = db.get(MobileParsedNutrition, food_id)
    food.name, food.amount, food.unit = body.name, body.quantity, body.unit
    food.amount_is_estimate = body.nutrition_source == "estimate"
    nutrition.quantity, nutrition.unit = body.quantity, body.unit
    nutrition.kcal_per_100g, nutrition.nutrition_source = body.kcal_per_100g, body.nutrition_source
    nutrition.portion_is_estimate = food.amount_is_estimate
    nutrition.note = "Исправлено вручную"
    db.commit()
    return {"status": "ok"}


@app.delete("/api/mobile/grok/foods/{food_id}", dependencies=[Auth])
def delete_food(food_id: int, db: Db):
    active_food(db, food_id).active = False
    db.commit()
    return {"status": "ok"}
