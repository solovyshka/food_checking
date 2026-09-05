from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.catalog.products import CatalogProduct, lookup_product
from app.catalog.units import CONSUMPTION_UNITS
from app.db.models import ConsumptionEntry, InventoryEntry, Product
from app.services.parser import ParsedInventory

MOSCOW = ZoneInfo("Europe/Moscow")

EntryKind = Literal["inventory", "consumption"]

KIND_LABELS = {
    "inventory": "Наличие",
    "consumption": "Съел",
}


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def get_or_create_product(db: Session, catalog: CatalogProduct) -> Product:
    normalized = normalize_name(catalog.name)
    product = db.scalar(select(Product).where(Product.name_normalized == normalized))
    if product:
        if product.unit != catalog.unit or product.name != catalog.name:
            product.unit = catalog.unit
            product.name = catalog.name
        return product
    product = Product(name=catalog.name, name_normalized=normalized, unit=catalog.unit)
    db.add(product)
    db.flush()
    return product


def get_or_create_named_product(db: Session, name: str, unit: str) -> Product:
    """Create a product from spoken name. Do not overwrite inventory catalog units."""
    normalized = normalize_name(name)
    product = db.scalar(select(Product).where(Product.name_normalized == normalized))
    if product:
        return product
    product = Product(name=name.strip(), name_normalized=normalized, unit=unit)
    db.add(product)
    db.flush()
    return product


def _model_for_kind(kind: EntryKind):
    return InventoryEntry if kind == "inventory" else ConsumptionEntry


@dataclass
class EntryRow:
    id: int
    product_name: str
    quantity: Decimal
    unit: str
    status: str
    entry_date: date
    recorded_at: datetime
    kcal_per_100g: Decimal | None = None


@dataclass
class PendingBatch:
    kind: EntryKind
    batch_id: str
    transcript: str
    recorded_at: datetime
    entry_date: date
    rows: list[EntryRow]
    unknown_names: list[str]
    missing_quantity: list[str]
    skipped: list[str]


def _map_inventory_items(
    parsed: ParsedInventory,
    *,
    db: Session | None,
    persist: bool,
    model,
    source: str,
    transcript: str,
    telegram_message_id: str | None,
    batch_id: str,
    now: datetime,
    entry_date: date,
) -> tuple[list[EntryRow], list[str], list[str], list[str]]:
    rows: list[EntryRow] = []
    unknown_names: list[str] = []
    missing_quantity: list[str] = []
    extra_skipped: list[str] = []
    for item in parsed.items:
        if item.quantity is None:
            missing_quantity.append(item.name)
            continue
        catalog = lookup_product(item.name)
        if catalog is None:
            unknown_names.append(item.name)
            continue
        if persist:
            assert db is not None
            product = get_or_create_product(db, catalog)
            entry = model(
                product_id=product.id,
                quantity=item.quantity,
                unit=product.unit,
                status="pending",
                source=source,
                transcript=transcript,
                telegram_message_id=telegram_message_id,
                batch_id=batch_id,
                recorded_at=now,
                entry_date=entry_date,
            )
            db.add(entry)
            db.flush()
            rows.append(
                EntryRow(
                    id=entry.id,
                    product_name=product.name,
                    quantity=entry.quantity,
                    unit=entry.unit,
                    status=entry.status,
                    entry_date=entry.entry_date,
                    recorded_at=entry.recorded_at,
                )
            )
        else:
            rows.append(
                EntryRow(
                    id=0,
                    product_name=catalog.name,
                    quantity=item.quantity,
                    unit=catalog.unit,
                    status="preview",
                    entry_date=entry_date,
                    recorded_at=now,
                )
            )
    return rows, unknown_names, missing_quantity, extra_skipped


def _map_consumption_items(
    parsed: ParsedInventory,
    *,
    db: Session | None,
    persist: bool,
    model,
    source: str,
    transcript: str,
    telegram_message_id: str | None,
    batch_id: str,
    now: datetime,
    entry_date: date,
) -> tuple[list[EntryRow], list[str], list[str], list[str]]:
    rows: list[EntryRow] = []
    unknown_names: list[str] = []
    missing_quantity: list[str] = []
    extra_skipped: list[str] = []
    for item in parsed.items:
        unit = (item.unit or "").strip()
        if unit not in CONSUMPTION_UNITS:
            extra_skipped.append(f"{item.name}: единица не г/мл")
            continue
        if item.quantity is None:
            missing_quantity.append(item.name)
            continue
        name = item.name.strip()
        if persist:
            assert db is not None
            product = get_or_create_named_product(db, name, unit)
            entry = model(
                product_id=product.id,
                quantity=item.quantity,
                unit=unit,
                kcal_per_100g=item.kcal_per_100g,
                status="pending",
                source=source,
                transcript=transcript,
                telegram_message_id=telegram_message_id,
                batch_id=batch_id,
                recorded_at=now,
                entry_date=entry_date,
            )
            db.add(entry)
            db.flush()
            rows.append(
                EntryRow(
                    id=entry.id,
                    product_name=product.name,
                    quantity=entry.quantity,
                    unit=entry.unit,
                    status=entry.status,
                    entry_date=entry.entry_date,
                    recorded_at=entry.recorded_at,
                    kcal_per_100g=entry.kcal_per_100g,
                )
            )
        else:
            rows.append(
                EntryRow(
                    id=0,
                    product_name=name,
                    quantity=item.quantity,
                    unit=unit,
                    status="preview",
                    entry_date=entry_date,
                    recorded_at=now,
                    kcal_per_100g=item.kcal_per_100g,
                )
            )
    return rows, unknown_names, missing_quantity, extra_skipped


def create_pending_entries(
    db: Session,
    parsed: ParsedInventory,
    transcript: str,
    kind: EntryKind = "inventory",
    recorded_at: datetime | None = None,
    telegram_message_id: str | None = None,
    source: str = "voice",
) -> PendingBatch:
    model = _model_for_kind(kind)
    now = recorded_at or datetime.now(MOSCOW)
    entry_date = parsed.entry_date or now.date()
    batch_id = str(uuid.uuid4())
    skipped = list(parsed.skipped)
    mapper = _map_consumption_items if kind == "consumption" else _map_inventory_items
    rows, unknown_names, missing_quantity, extra_skipped = mapper(
        parsed,
        db=db,
        persist=True,
        model=model,
        source=source,
        transcript=transcript,
        telegram_message_id=telegram_message_id,
        batch_id=batch_id,
        now=now,
        entry_date=entry_date,
    )
    skipped.extend(extra_skipped)

    problems = unknown_names or missing_quantity or skipped
    if not rows and not problems:
        skipped.append("ничего не распознано в тексте")
    if not rows:
        db.rollback()
        return PendingBatch(
            kind=kind,
            batch_id="",
            transcript=transcript,
            recorded_at=now,
            entry_date=entry_date,
            rows=[],
            unknown_names=unknown_names,
            missing_quantity=missing_quantity,
            skipped=skipped,
        )

    db.commit()
    return PendingBatch(
        kind=kind,
        batch_id=batch_id,
        transcript=transcript,
        recorded_at=now,
        entry_date=entry_date,
        rows=rows,
        unknown_names=unknown_names,
        missing_quantity=missing_quantity,
        skipped=skipped,
    )


def preview_parsed(
    parsed: ParsedInventory,
    transcript: str,
    kind: EntryKind = "inventory",
    recorded_at: datetime | None = None,
) -> PendingBatch:
    """Map parse result without writing to DB."""
    now = recorded_at or datetime.now(MOSCOW)
    entry_date = parsed.entry_date or now.date()
    skipped = list(parsed.skipped)
    mapper = _map_consumption_items if kind == "consumption" else _map_inventory_items
    rows, unknown_names, missing_quantity, extra_skipped = mapper(
        parsed,
        db=None,
        persist=False,
        model=None,
        source="preview",
        transcript=transcript,
        telegram_message_id=None,
        batch_id="",
        now=now,
        entry_date=entry_date,
    )
    skipped.extend(extra_skipped)

    if not rows and not (unknown_names or missing_quantity or skipped):
        skipped.append("ничего не распознано в тексте")

    return PendingBatch(
        kind=kind,
        batch_id="",
        transcript=transcript,
        recorded_at=now,
        entry_date=entry_date,
        rows=rows,
        unknown_names=unknown_names,
        missing_quantity=missing_quantity,
        skipped=skipped,
    )


def confirm_batch(db: Session, batch_id: str, kind: EntryKind = "inventory") -> int:
    model = _model_for_kind(kind)
    entries = db.scalars(
        select(model).where(model.batch_id == batch_id, model.status == "pending")
    ).all()
    now = datetime.now(MOSCOW)
    for entry in entries:
        entry.status = "confirmed"
        entry.confirmed_at = now
    db.commit()
    return len(entries)


def cancel_batch(db: Session, batch_id: str, kind: EntryKind = "inventory") -> int:
    model = _model_for_kind(kind)
    entries = db.scalars(
        select(model).where(model.batch_id == batch_id, model.status == "pending")
    ).all()
    for entry in entries:
        entry.status = "cancelled"
    db.commit()
    return len(entries)


def list_entries(
    db: Session,
    kind: EntryKind = "inventory",
    status: str | None = "confirmed",
) -> list[EntryRow]:
    model = _model_for_kind(kind)
    stmt = select(model).options(joinedload(model.product))
    if status:
        stmt = stmt.where(model.status == status)
    stmt = stmt.order_by(model.recorded_at.desc(), model.id.desc())
    entries = db.scalars(stmt).all()
    return [
        EntryRow(
            id=entry.id,
            product_name=entry.product.name,
            quantity=entry.quantity,
            unit=entry.unit,
            status=entry.status,
            entry_date=entry.entry_date,
            recorded_at=entry.recorded_at,
            kcal_per_100g=getattr(entry, "kcal_per_100g", None),
        )
        for entry in entries
    ]


def list_inventory(db: Session, status: str | None = "confirmed") -> list[EntryRow]:
    return list_entries(db, kind="inventory", status=status)


def list_consumption(db: Session, status: str | None = "confirmed") -> list[EntryRow]:
    return list_entries(db, kind="consumption", status=status)


def _fmt_kcal(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return format(value.normalize(), "f")


def format_pending_table(batch: PendingBatch) -> str:
    recorded = batch.recorded_at.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")
    label = KIND_LABELS[batch.kind]
    lines = [f"Режим: {label}", f"Дата: {recorded}"]
    if batch.rows:
        if batch.kind == "consumption":
            header = f"{'Продукт':<14} {'Кол-во':>7} {'Ед.':<4} {'ккал/100г':>9}"
            lines.extend(["```", header, "-" * len(header)])
            for row in batch.rows:
                qty = format(row.quantity.normalize(), "f")
                lines.append(
                    f"{row.product_name[:14]:<14} {qty:>7} {row.unit:<4} {_fmt_kcal(row.kcal_per_100g):>9}"
                )
        else:
            header = f"{'Продукт':<16} {'Кол-во':>8} {'Ед.':<12}"
            lines.extend(["```", header, "-" * 40])
            for row in batch.rows:
                qty = format(row.quantity.normalize(), "f")
                lines.append(f"{row.product_name[:16]:<16} {qty:>8} {row.unit:<12}")
        lines.append("```")
    if batch.unknown_names:
        lines.append(f"Нет в справочнике (не сохранено): {', '.join(batch.unknown_names)}")
    if batch.missing_quantity:
        lines.append(f"Не понял количество (не сохранено): {', '.join(batch.missing_quantity)}")
    if batch.skipped:
        lines.append(f"Не разобрал: {', '.join(batch.skipped)}")
    lines.append(f"Транскрипт: «{batch.transcript}»")
    return "\n".join(lines)


def format_entries_list(rows: list[EntryRow], kind: EntryKind = "inventory") -> str:
    label = KIND_LABELS[kind]
    if not rows:
        return f"Нет подтверждённых записей ({label})."
    if kind == "consumption":
        header = f"{'Дата':<11} {'Продукт':<14} {'Кол-во':>7} {'Ед.':<4} {'ккал/100г':>9}"
        lines = [f"{label}:", "```", header, "-" * len(header)]
        for row in rows[:50]:
            qty = format(row.quantity.normalize(), "f")
            lines.append(
                f"{row.entry_date.strftime('%d.%m.%Y'):<11} {row.product_name[:14]:<14} {qty:>7} {row.unit:<4} {_fmt_kcal(row.kcal_per_100g):>9}"
            )
        lines.append("```")
        return "\n".join(lines)
    lines = [
        f"{label}:",
        "```",
        f"{'Дата':<12} {'Продукт':<16} {'Кол-во':>8} {'Ед.':<8}",
        "-" * 48,
    ]
    for row in rows[:50]:
        qty = format(row.quantity.normalize(), "f")
        lines.append(
            f"{row.entry_date.strftime('%d.%m.%Y'):<12} {row.product_name[:16]:<16} {qty:>8} {row.unit:<8}"
        )
    lines.append("```")
    return "\n".join(lines)


def format_inventory_list(rows: list[EntryRow]) -> str:
    return format_entries_list(rows, kind="inventory")
