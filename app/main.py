from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.inventory import (
    PendingBatch,
    cancel_batch,
    confirm_batch,
    format_inventory_list,
    format_pending_table,
    list_inventory,
)
from app.services.voice_pipeline import process_voice_message

app = FastAPI(title="Food Checking API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/voice/process")
async def voice_process(
    file: UploadFile = File(...),
    telegram_message_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty audio file")
    try:
        batch = await process_voice_message(
            db=db,
            audio_bytes=audio,
            filename=file.filename or "voice.ogg",
            telegram_message_id=telegram_message_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
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
            }
            for row in batch.rows
        ],
    }


@app.get("/api/inventory")
def inventory_list(
    status: str | None = Query(default="confirmed"),
    db: Session = Depends(get_db),
) -> dict:
    rows = list_inventory(db, status=status)
    return {
        "status": status,
        "count": len(rows),
        "table": format_inventory_list(rows),
        "items": [
            {
                "id": row.id,
                "product_name": row.product_name,
                "quantity": str(row.quantity),
                "unit": row.unit,
                "entry_date": row.entry_date.isoformat(),
                "recorded_at": row.recorded_at.isoformat(),
            }
            for row in rows
        ],
    }


@app.post("/api/inventory/{batch_id}/confirm")
def inventory_confirm(batch_id: str, db: Session = Depends(get_db)) -> dict:
    count = confirm_batch(db, batch_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Pending batch not found")
    return {"batch_id": batch_id, "confirmed": count}


@app.post("/api/inventory/{batch_id}/cancel")
def inventory_cancel(batch_id: str, db: Session = Depends(get_db)) -> dict:
    count = cancel_batch(db, batch_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Pending batch not found")
    return {"batch_id": batch_id, "cancelled": count}
