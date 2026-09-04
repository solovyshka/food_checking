import httpx

from app.config import get_settings


class TranscriptionError(Exception):
    pass


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Local Whisper service."""
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


async def transcribe_audio_openai(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """OpenAI-compatible Audio Transcriptions (OpenAI / OpenRouter)."""
    from app.services.openai_client import openai_auth_headers

    settings = get_settings()
    if not settings.openai_api_key:
        raise TranscriptionError("OPENAI_API_KEY is not set")
    headers = openai_auth_headers(json_content=False)
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.openai_base_url.rstrip('/')}/audio/transcriptions",
            headers=headers,
            files={"file": (filename, audio_bytes, "audio/ogg")},
            data={
                "model": settings.openai_whisper_model,
                "language": "ru",
                "response_format": "json",
            },
        )
    if response.status_code != 200:
        raise TranscriptionError(
            f"Cloud STT error {response.status_code}: {response.text[:300]}"
        )
    text = (response.json().get("text") or "").strip()
    if not text:
        raise TranscriptionError("Cloud STT returned empty transcript")
    return text
