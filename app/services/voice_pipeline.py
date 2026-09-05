import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.inventory import EntryKind, PendingBatch, create_pending_entries
from app.services.parser import parse_inventory_text
from app.services.transcription import transcribe_for_pipeline
from app.services.transcripts import (
    TranscriptPreview,
    create_pending_inventory_transcript,
    create_pending_transcript,
)

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.1f}с" if seconds >= 1 else f"{seconds * 1000:.0f}мс"


async def process_text_as_transcript(
    db: Session,
    text: str,
    telegram_message_id: str | None = None,
) -> TranscriptPreview:
    recorded_at = datetime.now(MOSCOW)
    t0 = time.perf_counter()
    preview = create_pending_transcript(
        db=db,
        text=text,
        source="text",
        recorded_at=recorded_at,
        telegram_message_id=telegram_message_id,
    )
    db_s = time.perf_counter() - t0
    preview.timing_note = f"⏱ БД {_fmt_s(db_s)}"
    logger.info(
        "TIMING text_transcript chars=%s db=%.2fs id=%s period=%s %s",
        len(preview.text),
        db_s,
        preview.id,
        preview.entry_date,
        preview.meal_type,
    )
    return preview


async def process_voice_as_transcript(
    db: Session,
    audio_bytes: bytes,
    filename: str = "voice.ogg",
    telegram_message_id: str | None = None,
    *,
    kind: EntryKind = "consumption",
) -> TranscriptPreview:
    recorded_at = datetime.now(MOSCOW)
    t0 = time.perf_counter()
    transcript, backend, stt_detail = await transcribe_for_pipeline(
        audio_bytes, filename=filename
    )
    stt_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    if kind == "inventory":
        preview = create_pending_inventory_transcript(
            db=db,
            text=transcript,
            source="voice",
            recorded_at=recorded_at,
            telegram_message_id=telegram_message_id,
            stt_backend=backend,
        )
    else:
        preview = create_pending_transcript(
            db=db,
            text=transcript,
            source="voice",
            recorded_at=recorded_at,
            telegram_message_id=telegram_message_id,
            stt_backend=backend,
        )
    db_s = time.perf_counter() - t1
    stt_extra = f", {stt_detail}" if stt_detail else ""
    preview.timing_note = (
        f"⏱ STT {_fmt_s(stt_s)} ({backend}{stt_extra}) · БД {_fmt_s(db_s)}"
    )
    logger.info(
        "TIMING voice_transcript kind=%s stt=%.2fs backend=%s db=%.2fs "
        "audio_bytes=%s chars=%s id=%s",
        kind,
        stt_s,
        backend,
        db_s,
        len(audio_bytes),
        len(preview.text),
        preview.id,
    )
    return preview


async def process_text_message(
    db: Session,
    text: str,
    telegram_message_id: str | None = None,
    kind: EntryKind = "consumption",
) -> PendingBatch | TranscriptPreview:
    if kind == "consumption":
        return await process_text_as_transcript(
            db=db,
            text=text,
            telegram_message_id=telegram_message_id,
        )

    # Inventory text still goes through deferred path via voice-only bot;
    # keep eager parse for API callers that pass kind=inventory text.
    recorded_at = datetime.now(MOSCOW)
    transcript = text.strip()
    t0 = time.perf_counter()
    parsed, ollama = await parse_inventory_text(transcript)
    parse_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    batch = create_pending_entries(
        db=db,
        parsed=parsed,
        transcript=transcript,
        kind=kind,
        recorded_at=recorded_at,
        telegram_message_id=telegram_message_id,
        source="text",
    )
    db_s = time.perf_counter() - t1
    batch.timing_note = (
        f"⏱ Qwen {_fmt_s(parse_s)}"
        f"{f' {ollama}' if ollama else ''} · БД {_fmt_s(db_s)}"
    )
    logger.info(
        "TIMING text kind=%s parse=%.2fs db=%.2fs chars=%s ollama=%s",
        kind,
        parse_s,
        db_s,
        len(transcript),
        ollama,
    )
    return batch


async def process_voice_message(
    db: Session,
    audio_bytes: bytes,
    filename: str = "voice.ogg",
    telegram_message_id: str | None = None,
    kind: EntryKind = "inventory",
) -> PendingBatch | TranscriptPreview:
    # Both inventory and consumption: STT only, queue for later Qwen.
    return await process_voice_as_transcript(
        db=db,
        audio_bytes=audio_bytes,
        filename=filename,
        telegram_message_id=telegram_message_id,
        kind=kind,
    )
