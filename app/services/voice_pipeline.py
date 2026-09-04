from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.inventory import EntryKind, PendingBatch, create_pending_entries
from app.services.parser import parse_inventory_text
from app.services.transcription import transcribe_for_pipeline

MOSCOW = ZoneInfo("Europe/Moscow")


async def process_text_message(
    db: Session,
    text: str,
    telegram_message_id: str | None = None,
    kind: EntryKind = "consumption",
) -> PendingBatch:
    recorded_at = datetime.now(MOSCOW)
    transcript = text.strip()
    parsed = await parse_inventory_text(transcript)
    return create_pending_entries(
        db=db,
        parsed=parsed,
        transcript=transcript,
        kind=kind,
        recorded_at=recorded_at,
        telegram_message_id=telegram_message_id,
        source="text",
    )


async def process_voice_message(
    db: Session,
    audio_bytes: bytes,
    filename: str = "voice.ogg",
    telegram_message_id: str | None = None,
    kind: EntryKind = "inventory",
) -> PendingBatch:
    recorded_at = datetime.now(MOSCOW)
    # Ollama gets GigaAM transcript by default (Whisper only as fallback).
    transcript, _backend = await transcribe_for_pipeline(audio_bytes, filename=filename)
    parsed = await parse_inventory_text(transcript)
    return create_pending_entries(
        db=db,
        parsed=parsed,
        transcript=transcript,
        kind=kind,
        recorded_at=recorded_at,
        telegram_message_id=telegram_message_id,
        source="voice",
    )
