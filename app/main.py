from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.inventory import (
    EntryKind,
    cancel_batch,
    confirm_batch,
    format_entries_list,
    format_pending_table,
    list_consumption,
    list_inventory,
)
from app.services.voice_pipeline import process_text_message, process_voice_message

app = FastAPI(title="Food Checking API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _batch_response(batch) -> dict:
    return {
        "kind": batch.kind,
        "batch_id": batch.batch_id,
        "table": format_pending_table(batch),
        "transcript": batch.transcript,
        "entry_date": batch.entry_date.isoformat(),
        "recorded_at": batch.recorded_at.isoformat(),
        "items": [
            {
                "id": row.id,
                "product_name": row.product_name,
                "quantity": str(row.quantity),
                "unit": row.unit,
                "kcal_per_100g": (
                    str(row.kcal_per_100g) if row.kcal_per_100g is not None else None
                ),
            }
            for row in batch.rows
        ],
        "unknown_names": batch.unknown_names,
        "missing_quantity": batch.missing_quantity,
        "skipped": batch.skipped,
    }


@app.post("/api/voice/process")
async def voice_process(
    file: UploadFile = File(...),
    telegram_message_id: str | None = Query(default=None),
    kind: EntryKind = Query(default="inventory"),
    db: Session = Depends(get_db),
) -> dict:
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty audio file")
    if kind not in ("inventory", "consumption"):
        raise HTTPException(status_code=400, detail="kind must be inventory or consumption")
    try:
        batch = await process_voice_message(
            db=db,
            audio_bytes=audio,
            filename=file.filename or "voice.ogg",
            telegram_message_id=telegram_message_id,
            kind=kind,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _batch_response(batch)


@app.post("/api/text/process")
async def text_process(
    text: str = Query(..., min_length=1),
    telegram_message_id: str | None = Query(default=None),
    kind: EntryKind = Query(default="consumption"),
    db: Session = Depends(get_db),
) -> dict:
    if kind not in ("inventory", "consumption"):
        raise HTTPException(status_code=400, detail="kind must be inventory or consumption")
    try:
        batch = await process_text_message(
            db=db,
            text=text,
            telegram_message_id=telegram_message_id,
            kind=kind,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _batch_response(batch)


@app.get("/api/inventory")
def inventory_list(
    status: str | None = Query(default="confirmed"),
    db: Session = Depends(get_db),
) -> dict:
    rows = list_inventory(db, status=status)
    return {
        "kind": "inventory",
        "status": status,
        "count": len(rows),
        "table": format_entries_list(rows, kind="inventory"),
        "items": [
            {
                "id": row.id,
                "product_name": row.product_name,
                "quantity": str(row.quantity),
                "unit": row.unit,
                "kcal_per_100g": (
                    str(row.kcal_per_100g) if row.kcal_per_100g is not None else None
                ),
                "entry_date": row.entry_date.isoformat(),
                "recorded_at": row.recorded_at.isoformat(),
            }
            for row in rows
        ],
    }


@app.get("/api/consumption")
def consumption_list(
    status: str | None = Query(default="confirmed"),
    db: Session = Depends(get_db),
) -> dict:
    rows = list_consumption(db, status=status)
    return {
        "kind": "consumption",
        "status": status,
        "count": len(rows),
        "table": format_entries_list(rows, kind="consumption"),
        "items": [
            {
                "id": row.id,
                "product_name": row.product_name,
                "quantity": str(row.quantity),
                "unit": row.unit,
                "kcal_per_100g": (
                    str(row.kcal_per_100g) if row.kcal_per_100g is not None else None
                ),
                "entry_date": row.entry_date.isoformat(),
                "recorded_at": row.recorded_at.isoformat(),
            }
            for row in rows
        ],
    }


@app.post("/api/inventory/{batch_id}/confirm")
def inventory_confirm(batch_id: str, db: Session = Depends(get_db)) -> dict:
    count = confirm_batch(db, batch_id, kind="inventory")
    if count == 0:
        raise HTTPException(status_code=404, detail="Pending batch not found")
    return {"kind": "inventory", "batch_id": batch_id, "confirmed": count}


@app.post("/api/inventory/{batch_id}/cancel")
def inventory_cancel(batch_id: str, db: Session = Depends(get_db)) -> dict:
    count = cancel_batch(db, batch_id, kind="inventory")
    if count == 0:
        raise HTTPException(status_code=404, detail="Pending batch not found")
    return {"kind": "inventory", "batch_id": batch_id, "cancelled": count}


@app.post("/api/consumption/{batch_id}/confirm")
def consumption_confirm(batch_id: str, db: Session = Depends(get_db)) -> dict:
    count = confirm_batch(db, batch_id, kind="consumption")
    if count == 0:
        raise HTTPException(status_code=404, detail="Pending batch not found")
    return {"kind": "consumption", "batch_id": batch_id, "confirmed": count}


@app.post("/api/consumption/{batch_id}/cancel")
def consumption_cancel(batch_id: str, db: Session = Depends(get_db)) -> dict:
    count = cancel_batch(db, batch_id, kind="consumption")
    if count == 0:
        raise HTTPException(status_code=404, detail="Pending batch not found")
    return {"kind": "consumption", "batch_id": batch_id, "cancelled": count}
