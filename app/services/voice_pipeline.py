import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.inventory import EntryKind, PendingBatch, create_pending_entries
from app.services.parser import parse_consumption_text, parse_inventory_text
from app.services.transcription import transcribe_for_pipeline

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.1f}с" if seconds >= 1 else f"{seconds * 1000:.0f}мс"


async def process_text_message(
    db: Session,
    text: str,
    telegram_message_id: str | None = None,
    kind: EntryKind = "consumption",
) -> PendingBatch:
    recorded_at = datetime.now(MOSCOW)
    transcript = text.strip()
    parse = parse_consumption_text if kind == "consumption" else parse_inventory_text
    t0 = time.perf_counter()
    parsed, ollama = await parse(transcript)
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
) -> PendingBatch:
    recorded_at = datetime.now(MOSCOW)
    t0 = time.perf_counter()
    transcript, backend, stt_detail = await transcribe_for_pipeline(
        audio_bytes, filename=filename
    )
    stt_s = time.perf_counter() - t0
    parse = parse_consumption_text if kind == "consumption" else parse_inventory_text
    t1 = time.perf_counter()
    parsed, ollama = await parse(transcript)
    parse_s = time.perf_counter() - t1
    t2 = time.perf_counter()
    batch = create_pending_entries(
        db=db,
        parsed=parsed,
        transcript=transcript,
        kind=kind,
        recorded_at=recorded_at,
        telegram_message_id=telegram_message_id,
        source="voice",
    )
    db_s = time.perf_counter() - t2
    stt_extra = f", {stt_detail}" if stt_detail else ""
    batch.timing_note = (
        f"⏱ STT {_fmt_s(stt_s)} ({backend}{stt_extra}) · "
        f"Qwen {_fmt_s(parse_s)}"
        f"{f' {ollama}' if ollama else ''} · БД {_fmt_s(db_s)}"
    )
    logger.info(
        "TIMING voice kind=%s stt=%.2fs backend=%s parse=%.2fs db=%.2fs "
        "audio_bytes=%s chars=%s ollama=%s",
        kind,
        stt_s,
        backend,
        parse_s,
        db_s,
        len(audio_bytes),
        len(transcript),
        ollama,
    )
    return batch
