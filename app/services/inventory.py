import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db.models import InventoryEntry, Product
from app.services.parser import ParsedInventory, ParsedItem

MOSCOW = ZoneInfo("Europe/Moscow")


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip().lower())
    return cleaned


def get_or_create_product(db: Session, name: str) -> Product:
    normalized = normalize_name(name)
    product = db.scalar(select(Product).where(Product.name_normalized == normalized))
    if product:
        return product
    product = Product(name=name.strip(), name_normalized=normalized)
    db.add(product)
    db.flush()
    return product


@dataclass
class InventoryRow:
    id: int
    product_name: str
    quantity: Decimal
    unit: str
    status: str
    entry_date: date
    recorded_at: datetime


@dataclass
class PendingBatch:
    batch_id: str
    transcript: str
    recorded_at: datetime
    entry_date: date
    rows: list[InventoryRow]


def create_pending_entries(
    db: Session,
    parsed: ParsedInventory,
    transcript: str,
    recorded_at: datetime | None = None,
    telegram_message_id: str | None = None,
) -> PendingBatch:
    now = recorded_at or datetime.now(MOSCOW)
    entry_date = parsed.entry_date or now.date()
    batch_id = str(uuid.uuid4())
    rows: list[InventoryRow] = []

    for item in parsed.items:
        product = get_or_create_product(db, item.name)
        entry = InventoryEntry(
            product_id=product.id,
            quantity=item.quantity,
            unit=item.unit,
            status="pending",
            source="voice",
            transcript=transcript,
            telegram_message_id=telegram_message_id,
            batch_id=batch_id,
            recorded_at=now,
            entry_date=entry_date,
        )
        db.add(entry)
        db.flush()
        rows.append(
            InventoryRow(
                id=entry.id,
                product_name=product.name,
                quantity=entry.quantity,
                unit=entry.unit,
                status=entry.status,
                entry_date=entry.entry_date,
                recorded_at=entry.recorded_at,
            )
        )

    db.commit()
    return PendingBatch(
        batch_id=batch_id,
        transcript=transcript,
        recorded_at=now,
        entry_date=entry_date,
        rows=rows,
    )


def confirm_batch(db: Session, batch_id: str) -> int:
    entries = db.scalars(
        select(InventoryEntry).where(
            InventoryEntry.batch_id == batch_id,
            InventoryEntry.status == "pending",
        )
    ).all()
    now = datetime.now(MOSCOW)
    for entry in entries:
        entry.status = "confirmed"
        entry.confirmed_at = now
    db.commit()
    return len(entries)


def cancel_batch(db: Session, batch_id: str) -> int:
    entries = db.scalars(
        select(InventoryEntry).where(
            InventoryEntry.batch_id == batch_id,
            InventoryEntry.status == "pending",
        )
    ).all()
    for entry in entries:
        entry.status = "cancelled"
    db.commit()
    return len(entries)


def list_inventory(db: Session, status: str | None = "confirmed") -> list[InventoryRow]:
    stmt = select(InventoryEntry).options(joinedload(InventoryEntry.product))
    if status:
        stmt = stmt.where(InventoryEntry.status == status)
    stmt = stmt.order_by(InventoryEntry.recorded_at.desc(), InventoryEntry.id.desc())
    entries = db.scalars(stmt).all()
    return [
        InventoryRow(
            id=entry.id,
            product_name=entry.product.name,
            quantity=entry.quantity,
            unit=entry.unit,
            status=entry.status,
            entry_date=entry.entry_date,
            recorded_at=entry.recorded_at,
        )
        for entry in entries
    ]


def format_pending_table(batch: PendingBatch) -> str:
    recorded = batch.recorded_at.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")
    lines = [
        f"Дата: {recorded}",
        "```",
        f"{'Продукт':<16} {'Кол-во':>8} {'Ед.':<8}",
        "-" * 36,
    ]
    for row in batch.rows:
        qty = format(row.quantity.normalize(), "f")
        lines.append(f"{row.product_name[:16]:<16} {qty:>8} {row.unit:<8}")
    lines.append("```")
    lines.append(f"Транскрипт: «{batch.transcript}»")
    return "\n".join(lines)


def format_inventory_list(rows: list[InventoryRow]) -> str:
    if not rows:
        return "Нет подтверждённых записей."
    lines = ["```", f"{'Дата':<12} {'Продукт':<16} {'Кол-во':>8} {'Ед.':<8}", "-" * 48]
    for row in rows[:50]:
        qty = format(row.quantity.normalize(), "f")
        lines.append(
            f"{row.entry_date.strftime('%d.%m.%Y'):<12} {row.product_name[:16]:<16} {qty:>8} {row.unit:<8}"
        )
    lines.append("```")
    return "\n".join(lines)
