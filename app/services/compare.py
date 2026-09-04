"""Side-by-side local vs OpenAI food-diary comparison — never writes to DB."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.food_diary import (
    FoodDiaryResult,
    extract_food_diary,
    format_food_diary,
)
from app.services.hideme_vpn import openai_vpn_session
from app.services.inventory import PendingBatch, preview_parsed
from app.services.parser import ParseError, parse_inventory_text
from app.services.transcription import (
    TranscriptionError,
    transcribe_audio,
    transcribe_audio_openai,
)

MOSCOW = ZoneInfo("Europe/Moscow")


@dataclass
class CompareResult:
    local_stt: str
    openai_stt: str | None
    openai_stt_error: str | None
    local_preview: PendingBatch
    food_diary: FoodDiaryResult | None
    openai_parse_error: str | None
    recorded_at: datetime


def _format_preview_block(title: str, batch: PendingBatch) -> list[str]:
    lines = [f"*{title}*"]
    if batch.rows:
        lines.append("```")
        lines.append(f"{'Продукт':<16} {'Кол-во':>8} {'Ед.':<10}")
        lines.append("-" * 36)
        for row in batch.rows:
            qty = format(row.quantity.normalize(), "f")
            lines.append(f"{row.product_name[:16]:<16} {qty:>8} {row.unit:<10}")
        lines.append("```")
    else:
        lines.append("_пустой результат_")
    if batch.unknown_names:
        lines.append(f"нет в справочнике: {', '.join(batch.unknown_names)}")
    if batch.missing_quantity:
        lines.append(f"без количества: {', '.join(batch.missing_quantity)}")
    if batch.skipped:
        lines.append(f"пропущено: {', '.join(batch.skipped)}")
    return lines


def format_compare_message(result: CompareResult) -> str:
    lines = [
        "Сравнение (в БД *не* пишем)",
        "",
        f"STT local: «{result.local_stt}»",
    ]
    if result.openai_stt is not None:
        lines.append(f"STT OpenAI: «{result.openai_stt}»")
    elif result.openai_stt_error:
        lines.append(f"STT OpenAI: _{result.openai_stt_error}_")

    lines.append("")
    lines.extend(_format_preview_block("Парсер local (Ollama)", result.local_preview))
    lines.append("")
    if result.food_diary is not None:
        lines.append(format_food_diary(result.food_diary))
    else:
        err = result.openai_parse_error or "нет результата"
        lines.append(f"*Дневник OpenAI*\n_{err}_")

    text = "\n".join(lines)
    if len(text) > 3900:
        return text[:3900] + "\n…(обрезано)"
    return text


async def _run_openai_diary(transcript: str) -> tuple[FoodDiaryResult | None, str | None]:
    settings = get_settings()
    if not settings.has_openai:
        return None, "OPENAI_API_KEY не задан"
    try:
        return await extract_food_diary(transcript), None
    except ParseError as exc:
        return None, str(exc)


async def compare_from_text(text: str) -> CompareResult:
    recorded_at = datetime.now(MOSCOW)
    transcript = text.strip()

    local_parsed = await parse_inventory_text(transcript)
    local_preview = preview_parsed(local_parsed, transcript, recorded_at=recorded_at)

    async with openai_vpn_session():
        food_diary, openai_parse_error = await _run_openai_diary(transcript)

    return CompareResult(
        local_stt=transcript,
        openai_stt=None,
        openai_stt_error=None,
        local_preview=local_preview,
        food_diary=food_diary,
        openai_parse_error=openai_parse_error,
        recorded_at=recorded_at,
    )


async def compare_from_voice(
    audio_bytes: bytes,
    filename: str = "voice.ogg",
) -> CompareResult:
    settings = get_settings()
    recorded_at = datetime.now(MOSCOW)

    local_stt = await transcribe_audio(audio_bytes, filename=filename)

    openai_stt: str | None = None
    openai_stt_error: str | None = None
    food_diary: FoodDiaryResult | None = None
    openai_parse_error: str | None = None

    # Local Ollama in parallel with OpenAI (VPN only wraps cloud calls).
    local_task = asyncio.create_task(parse_inventory_text(local_stt))
    try:
        async with openai_vpn_session():
            if settings.has_openai:
                stt_task = asyncio.create_task(
                    transcribe_audio_openai(audio_bytes, filename=filename)
                )
                diary_task = asyncio.create_task(_run_openai_diary(local_stt))
                stt_outcome, diary_outcome = await asyncio.gather(
                    stt_task, diary_task, return_exceptions=True
                )
                if isinstance(stt_outcome, Exception):
                    openai_stt_error = str(stt_outcome)
                else:
                    openai_stt = stt_outcome
                if isinstance(diary_outcome, Exception):
                    openai_parse_error = str(diary_outcome)
                else:
                    food_diary, openai_parse_error = diary_outcome
            else:
                openai_stt_error = "OPENAI_API_KEY не задан"
                openai_parse_error = "OPENAI_API_KEY не задан"
    finally:
        local_parsed = await local_task

    if isinstance(local_parsed, Exception):
        raise local_parsed

    local_preview = preview_parsed(local_parsed, local_stt, recorded_at=recorded_at)

    return CompareResult(
        local_stt=local_stt,
        openai_stt=openai_stt,
        openai_stt_error=openai_stt_error,
        local_preview=local_preview,
        food_diary=food_diary,
        openai_parse_error=openai_parse_error,
        recorded_at=recorded_at,
    )
