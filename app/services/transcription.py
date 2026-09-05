import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    pass


def _stt_detail_from_payload(payload: dict) -> str:
    parts = []
    ffmpeg = payload.get("ffmpeg_s")
    infer = payload.get("infer_s")
    try:
        if ffmpeg is not None:
            parts.append(f"ffmpeg {float(ffmpeg):.1f}с")
        if infer is not None:
            parts.append(f"infer {float(infer):.1f}с")
    except (TypeError, ValueError):
        return ""
    return ", ".join(parts)


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Local Whisper service."""
    settings = get_settings()
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=600.0) as client:
        response = await client.post(
            f"{settings.whisper_url.rstrip('/')}/transcribe",
            files={"file": (filename, audio_bytes, "audio/ogg")},
            data={"language": "ru"},
        )
    elapsed = time.perf_counter() - t0
    logger.info("TIMING whisper http=%.2fs bytes=%s", elapsed, len(audio_bytes))
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


async def transcribe_audio_gigaam(
    audio_bytes: bytes, filename: str = "voice.ogg"
) -> tuple[str, str]:
    """Local GigaAM service (https://github.com/salute-developers/GigaAM)."""
    settings = get_settings()
    if not settings.gigaam_enabled:
        raise TranscriptionError("GigaAM disabled (GIGAAM_ENABLED=0)")
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=600.0) as client:
        response = await client.post(
            f"{settings.gigaam_url.rstrip('/')}/transcribe",
            files={"file": (filename, audio_bytes, "audio/ogg")},
            data={"language": "ru"},
        )
    elapsed = time.perf_counter() - t0
    if response.status_code != 200:
        raise TranscriptionError(
            f"GigaAM error {response.status_code}: {response.text[:300]}"
        )
    payload = response.json()
    text = (payload.get("text") or "").strip()
    if not text:
        raise TranscriptionError("GigaAM returned empty transcript")
    detail = _stt_detail_from_payload(payload)
    logger.info(
        "TIMING gigaam http=%.2fs bytes=%s detail=%s",
        elapsed,
        len(audio_bytes),
        detail or "-",
    )
    return text, detail


async def transcribe_for_pipeline(
    audio_bytes: bytes,
    filename: str = "voice.ogg",
) -> tuple[str, str, str]:
    """STT for production pipeline. Returns (text, backend_name, detail).

    Default: GigaAM, with Whisper fallback if GigaAM fails or is disabled.
    """
    settings = get_settings()
    backend = (settings.voice_stt_backend or "gigaam").strip().lower()
    if backend == "whisper":
        return await transcribe_audio(audio_bytes, filename=filename), "whisper", ""
    if backend != "gigaam":
        raise TranscriptionError(f"Unknown VOICE_STT_BACKEND={backend!r}")
    try:
        text, detail = await transcribe_audio_gigaam(audio_bytes, filename=filename)
        return text, "gigaam", detail
    except TranscriptionError:
        if not settings.gigaam_enabled:
            text = await transcribe_audio(audio_bytes, filename=filename)
            return text, "whisper", ""
        text = await transcribe_audio(audio_bytes, filename=filename)
        return text, "whisper-fallback", ""
