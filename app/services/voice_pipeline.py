from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.inventory import PendingBatch, create_pending_entries
from app.services.parser import parse_inventory_text
from app.services.transcription import transcribe_audio

MOSCOW = ZoneInfo("Europe/Moscow")


async def process_voice_message(
    db: Session,
    audio_bytes: bytes,
    filename: str = "voice.ogg",
    telegram_message_id: str | None = None,
) -> PendingBatch:
    recorded_at = datetime.now(MOSCOW)
    transcript = await transcribe_audio(audio_bytes, filename=filename)
    parsed = await parse_inventory_text(transcript)
    return create_pending_entries(
        db=db,
        parsed=parsed,
        transcript=transcript,
        recorded_at=recorded_at,
        telegram_message_id=telegram_message_id,
    )
