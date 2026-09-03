import httpx

from app.config import get_settings


class TranscriptionError(Exception):
    pass


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=600.0) as client:
        response = await client.post(
            f"{settings.whisper_url.rstrip('/')}/transcribe",
            files={"file": (filename, audio_bytes, "audio/ogg")},
            data={"language": "ru"},
        )
    if response.status_code != 200:
        raise TranscriptionError(f"Whisper error {response.status_code}: {response.text}")
    payload = response.json()
    text = (payload.get("text") or "").strip()
    if not text:
        raise TranscriptionError("Whisper returned empty transcript")
    return text
