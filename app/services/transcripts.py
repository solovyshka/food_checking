from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ConsumptionTranscript, InventoryTranscript
from app.services.inventory import (
    PendingBatch,
    cancel_batch,
    confirm_batch,
    create_pending_entries,
    preview_parsed,
)
from app.services.parser import parse_consumption_text, parse_inventory_text

MOSCOW = ZoneInfo("Europe/Moscow")

MealType = Literal["завтрак", "обед", "ужин", "перекус"]
TranscriptSource = Literal["voice", "text"]

MEAL_TYPES: tuple[MealType, ...] = ("завтрак", "обед", "ужин", "перекус")


@dataclass
class TranscriptPreview:
    id: int
    text: str
    source: str
    entry_date: date
    recorded_at: datetime
    meal_type: str | None = None
    kind: Literal["consumption", "inventory"] = "consumption"
    stt_backend: str | None = None
    timing_note: str = ""


@dataclass
class QueuedPeriod:
    entry_date: date
    meal_type: str
    count: int


@dataclass
class QueuedInventoryDay:
    entry_date: date
    count: int


def infer_meal_type(dt: datetime) -> MealType:
    local = dt.astimezone(MOSCOW) if dt.tzinfo else dt.replace(tzinfo=MOSCOW)
    hour = local.hour
    if 5 <= hour < 11:
        return "завтрак"
    if 11 <= hour < 16:
        return "обед"
    if 16 <= hour < 22:
        return "ужин"
    return "перекус"


def format_period(entry_date: date, meal_type: str) -> str:
    return f"{entry_date.strftime('%d.%m.%Y')} · {meal_type}"


def create_pending_transcript(
    db: Session,
    text: str,
    *,
    source: TranscriptSource = "voice",
    recorded_at: datetime | None = None,
    telegram_message_id: str | None = None,
    stt_backend: str | None = None,
) -> TranscriptPreview:
    now = recorded_at or datetime.now(MOSCOW)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW)
    local = now.astimezone(MOSCOW)
    meal = infer_meal_type(local)
    entry = ConsumptionTranscript(
        text=text.strip(),
        status="pending",
        source=source,
        meal_type=meal,
        entry_date=local.date(),
        recorded_at=local,
        telegram_message_id=telegram_message_id,
        stt_backend=stt_backend,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return TranscriptPreview(
        id=entry.id,
        text=entry.text,
        source=entry.source,
        meal_type=entry.meal_type,
        entry_date=entry.entry_date,
        recorded_at=entry.recorded_at,
        kind="consumption",
        stt_backend=entry.stt_backend,
    )


def confirm_transcript(db: Session, transcript_id: int) -> TranscriptPreview | None:
    entry = db.get(ConsumptionTranscript, transcript_id)
    if entry is None or entry.status != "pending":
        return None
    entry.status = "queued"
    entry.confirmed_at = datetime.now(MOSCOW)
    db.commit()
    db.refresh(entry)
    return TranscriptPreview(
        id=entry.id,
        text=entry.text,
        source=entry.source,
        meal_type=entry.meal_type,
        entry_date=entry.entry_date,
        recorded_at=entry.recorded_at,
        kind="consumption",
        stt_backend=entry.stt_backend,
    )


def cancel_transcript(db: Session, transcript_id: int) -> bool:
    entry = db.get(ConsumptionTranscript, transcript_id)
    if entry is None or entry.status != "pending":
        return False
    entry.status = "cancelled"
    db.commit()
    return True


def list_queued_periods(db: Session) -> list[QueuedPeriod]:
    rows = db.execute(
        select(
            ConsumptionTranscript.entry_date,
            ConsumptionTranscript.meal_type,
            func.count(ConsumptionTranscript.id),
        )
        .where(
            ConsumptionTranscript.status == "queued",
            ConsumptionTranscript.parse_batch_id.is_(None),
        )
        .group_by(ConsumptionTranscript.entry_date, ConsumptionTranscript.meal_type)
        .order_by(ConsumptionTranscript.entry_date.desc(), ConsumptionTranscript.meal_type)
    ).all()
    return [
        QueuedPeriod(entry_date=row[0], meal_type=row[1], count=int(row[2]))
        for row in rows
    ]


def list_queued_for_period(
    db: Session,
    entry_date: date,
    meal_type: str,
) -> list[ConsumptionTranscript]:
    return list(
        db.scalars(
            select(ConsumptionTranscript)
            .where(
                ConsumptionTranscript.status == "queued",
                ConsumptionTranscript.parse_batch_id.is_(None),
                ConsumptionTranscript.entry_date == entry_date,
                ConsumptionTranscript.meal_type == meal_type,
            )
            .order_by(ConsumptionTranscript.recorded_at.asc(), ConsumptionTranscript.id.asc())
        ).all()
    )


def _combine_transcripts(entries: list[ConsumptionTranscript]) -> str:
    parts: list[str] = []
    for entry in entries:
        stamp = entry.recorded_at.astimezone(MOSCOW).strftime("%H:%M")
        parts.append(f"[{stamp}] {entry.text.strip()}")
    return "\n".join(parts)


async def parse_period(
    db: Session,
    entry_date: date,
    meal_type: str,
) -> PendingBatch | None:
    entries = list_queued_for_period(db, entry_date, meal_type)
    if not entries:
        return None

    combined = _combine_transcripts(entries)
    parsed, ollama = await parse_consumption_text(combined)
    # Force period date from the queue group (not free-form from model).
    parsed.entry_date = entry_date

    recorded_at = entries[-1].recorded_at
    batch = create_pending_entries(
        db=db,
        parsed=parsed,
        transcript=combined,
        kind="consumption",
        recorded_at=recorded_at,
        telegram_message_id=None,
        source="voice",
    )
    if not batch.batch_id:
        return batch

    for entry in entries:
        entry.parse_batch_id = batch.batch_id
    db.commit()

    period = format_period(entry_date, meal_type)
    note = f"Период: {period} · записей {len(entries)}"
    if ollama:
        note += f" · {ollama}"
    batch.timing_note = (
        f"{batch.timing_note} · {note}" if batch.timing_note else f"⏱ {note}"
    )
    return batch


def finalize_parse(db: Session, batch_id: str, *, confirm: bool) -> int:
    """Confirm/cancel consumption batch and update linked transcripts."""
    if confirm:
        count = confirm_batch(db, batch_id, kind="consumption")
        if count == 0:
            return 0
        entries = db.scalars(
            select(ConsumptionTranscript).where(
                ConsumptionTranscript.parse_batch_id == batch_id,
                ConsumptionTranscript.status == "queued",
            )
        ).all()
        now = datetime.now(MOSCOW)
        for entry in entries:
            entry.status = "parsed"
            entry.confirmed_at = entry.confirmed_at or now
        db.commit()
        return count

    count = cancel_batch(db, batch_id, kind="consumption")
    entries = db.scalars(
        select(ConsumptionTranscript).where(
            ConsumptionTranscript.parse_batch_id == batch_id,
            ConsumptionTranscript.status == "queued",
        )
    ).all()
    for entry in entries:
        entry.parse_batch_id = None
    db.commit()
    return count


def format_transcript_preview(preview: TranscriptPreview) -> str:
    recorded = preview.recorded_at.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")
    if preview.kind == "inventory":
        lines = [
            "Режим: Наличие — проверка текста",
            f"Дата записи: {recorded}",
            f"День: {preview.entry_date.strftime('%d.%m.%Y')}",
            f"Текст: «{preview.text}»",
        ]
    else:
        period = format_period(preview.entry_date, preview.meal_type or "перекус")
        lines = [
            "Режим: Съел — проверка текста",
            f"Дата записи: {recorded}",
            f"Период: {period}",
            f"Текст: «{preview.text}»",
        ]
    if preview.timing_note:
        lines.append(preview.timing_note)
    return "\n".join(lines)


# --- inventory deferred transcripts ---


def create_pending_inventory_transcript(
    db: Session,
    text: str,
    *,
    source: TranscriptSource = "voice",
    recorded_at: datetime | None = None,
    telegram_message_id: str | None = None,
    stt_backend: str | None = None,
) -> TranscriptPreview:
    now = recorded_at or datetime.now(MOSCOW)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW)
    local = now.astimezone(MOSCOW)
    entry = InventoryTranscript(
        text=text.strip(),
        status="pending",
        source=source,
        entry_date=local.date(),
        recorded_at=local,
        telegram_message_id=telegram_message_id,
        stt_backend=stt_backend,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return TranscriptPreview(
        id=entry.id,
        text=entry.text,
        source=entry.source,
        entry_date=entry.entry_date,
        recorded_at=entry.recorded_at,
        kind="inventory",
        stt_backend=entry.stt_backend,
    )


def confirm_inventory_transcript(
    db: Session, transcript_id: int
) -> TranscriptPreview | None:
    entry = db.get(InventoryTranscript, transcript_id)
    if entry is None or entry.status != "pending":
        return None
    entry.status = "queued"
    entry.confirmed_at = datetime.now(MOSCOW)
    db.commit()
    db.refresh(entry)
    return TranscriptPreview(
        id=entry.id,
        text=entry.text,
        source=entry.source,
        entry_date=entry.entry_date,
        recorded_at=entry.recorded_at,
        kind="inventory",
        stt_backend=entry.stt_backend,
    )


def cancel_inventory_transcript(db: Session, transcript_id: int) -> bool:
    entry = db.get(InventoryTranscript, transcript_id)
    if entry is None or entry.status != "pending":
        return False
    entry.status = "cancelled"
    db.commit()
    return True


def list_queued_inventory_days(db: Session) -> list[QueuedInventoryDay]:
    rows = db.execute(
        select(
            InventoryTranscript.entry_date,
            func.count(InventoryTranscript.id),
        )
        .where(
            InventoryTranscript.status == "queued",
            InventoryTranscript.parse_batch_id.is_(None),
        )
        .group_by(InventoryTranscript.entry_date)
        .order_by(InventoryTranscript.entry_date.desc())
    ).all()
    return [
        QueuedInventoryDay(entry_date=row[0], count=int(row[1])) for row in rows
    ]


def list_queued_inventory_for_day(
    db: Session,
    entry_date: date,
) -> list[InventoryTranscript]:
    return list(
        db.scalars(
            select(InventoryTranscript)
            .where(
                InventoryTranscript.status == "queued",
                InventoryTranscript.parse_batch_id.is_(None),
                InventoryTranscript.entry_date == entry_date,
            )
            .order_by(
                InventoryTranscript.recorded_at.asc(),
                InventoryTranscript.id.asc(),
            )
        ).all()
    )


def _combine_inventory_transcripts(entries: list[InventoryTranscript]) -> str:
    parts: list[str] = []
    for entry in entries:
        stamp = entry.recorded_at.astimezone(MOSCOW).strftime("%H:%M")
        parts.append(f"[{stamp}] {entry.text.strip()}")
    return "\n".join(parts)


async def parse_inventory_day(
    db: Session,
    entry_date: date,
) -> PendingBatch | None:
    """Parse queued transcripts for a day into a preview batch (no pending DB rows).

    Transcripts are marked parsed immediately; the proposal is written to Google Sheets
    by the bot. Confirmed inventory is imported later via «Добавить в БД».
    """
    entries = list_queued_inventory_for_day(db, entry_date)
    if not entries:
        return None

    combined = _combine_inventory_transcripts(entries)
    parsed, ollama = await parse_inventory_text(combined)
    parsed.entry_date = entry_date

    recorded_at = entries[-1].recorded_at
    batch = preview_parsed(
        parsed=parsed,
        transcript=combined,
        kind="inventory",
        recorded_at=recorded_at,
    )
    batch_id = str(uuid.uuid4())
    batch.batch_id = batch_id

    now = datetime.now(MOSCOW)
    for entry in entries:
        entry.parse_batch_id = batch_id
        entry.status = "parsed"
        entry.confirmed_at = entry.confirmed_at or now
    db.commit()

    day = entry_date.strftime("%d.%m.%Y")
    note = f"День: {day} · записей {len(entries)} · → Sheets"
    if ollama:
        note += f" · {ollama}"
    batch.timing_note = (
        f"{batch.timing_note} · {note}" if batch.timing_note else f"⏱ {note}"
    )
    return batch


def finalize_inventory_parse(db: Session, batch_id: str, *, confirm: bool) -> int:
    """Legacy confirm/cancel for pending inventory batches (API / old UI)."""
    if confirm:
        count = confirm_batch(db, batch_id, kind="inventory")
        if count == 0:
            return 0
        entries = db.scalars(
            select(InventoryTranscript).where(
                InventoryTranscript.parse_batch_id == batch_id,
                InventoryTranscript.status == "queued",
            )
        ).all()
        now = datetime.now(MOSCOW)
        for entry in entries:
            entry.status = "parsed"
            entry.confirmed_at = entry.confirmed_at or now
        db.commit()
        return count

    count = cancel_batch(db, batch_id, kind="inventory")
    entries = db.scalars(
        select(InventoryTranscript).where(
            InventoryTranscript.parse_batch_id == batch_id,
            InventoryTranscript.status == "queued",
        )
    ).all()
    for entry in entries:
        entry.parse_batch_id = None
    db.commit()
    return count
