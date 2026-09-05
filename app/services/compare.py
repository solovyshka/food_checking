"""Side-by-side local vs OpenAI comparison — never writes to DB."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.food_diary import (
    FoodDiaryResult,
    extract_food_diary,
    format_food_diary,
)
from app.services.hideme_vpn import openai_vpn_session
from app.services.inventory import PendingBatch, preview_parsed
from app.services.parser import (
    ParseError,
    parse_consumption_text,
    parse_inventory_text,
    parse_inventory_text_openai,
)
from app.services.transcription import (
    TranscriptionError,
    transcribe_audio,
    transcribe_audio_gigaam,
    transcribe_audio_openai,
)

MOSCOW = ZoneInfo("Europe/Moscow")

CompareKind = Literal["inventory", "consumption"]


@dataclass
class CompareResult:
    kind: CompareKind
    local_stt: str
    gigaam_stt: str | None
    gigaam_stt_error: str | None
    openai_stt: str | None
    openai_stt_error: str | None
    local_preview: PendingBatch
    openai_preview: PendingBatch | None
    food_diary: FoodDiaryResult | None
    openai_parse_error: str | None
    recorded_at: datetime


def _format_preview_block(title: str, batch: PendingBatch) -> list[str]:
    lines = [f"*{title}*"]
    if batch.rows:
        lines.append("```")
        if batch.kind == "consumption":
            lines.append(f"{'Продукт':<14} {'Кол-во':>7} {'Ед.':<4} {'ккал/100г':>9}")
            lines.append("-" * 38)
            for row in batch.rows:
                qty = format(row.quantity.normalize(), "f")
                kcal = (
                    "—"
                    if row.kcal_per_100g is None
                    else format(row.kcal_per_100g.normalize(), "f")
                )
                lines.append(
                    f"{row.product_name[:14]:<14} {qty:>7} {row.unit:<4} {kcal:>9}"
                )
        else:
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
    title = (
        "Сравнение *наличия*"
        if result.kind == "inventory"
        else "Сравнение *съел* (дневник)"
    )
    lines = [
        f"{title} (в БД *не* пишем)",
        "",
        f"STT Whisper: «{result.local_stt}»",
    ]
    if result.gigaam_stt is not None:
        lines.append(f"STT GigaAM: «{result.gigaam_stt}»")
    elif result.gigaam_stt_error:
        lines.append(f"STT GigaAM: _{result.gigaam_stt_error}_")
    if result.openai_stt is not None:
        lines.append(f"STT OpenAI: «{result.openai_stt}»")
    elif result.openai_stt_error:
        lines.append(f"STT OpenAI: _{result.openai_stt_error}_")

    lines.append("")
    lines.extend(
        _format_preview_block(
            "Парсер local (Ollama, по GigaAM)", result.local_preview
        )
    )
    lines.append("")

    if result.kind == "inventory":
        if result.openai_preview is not None:
            lines.extend(
                _format_preview_block("Парсер OpenAI (наличие)", result.openai_preview)
            )
        else:
            err = result.openai_parse_error or "нет результата"
            lines.append(f"*Парсер OpenAI (наличие)*\n_{err}_")
    else:
        if result.food_diary is not None:
            lines.append(format_food_diary(result.food_diary))
        else:
            err = result.openai_parse_error or "нет результата"
            lines.append(f"*Дневник OpenAI*\n_{err}_")

    text = "\n".join(lines)
    if len(text) > 3900:
        return text[:3900] + "\n…(обрезано)"
    return text


async def _run_openai_inventory(
    transcript: str,
) -> tuple[PendingBatch | None, str | None]:
    settings = get_settings()
    if not settings.has_openai:
        return None, "OPENAI_API_KEY не задан"
    try:
        parsed = await parse_inventory_text_openai(transcript)
        return preview_parsed(parsed, transcript, kind="inventory"), None
    except ParseError as exc:
        return None, str(exc)


async def _run_openai_diary(transcript: str) -> tuple[FoodDiaryResult | None, str | None]:
    settings = get_settings()
    if not settings.has_openai:
        return None, "OPENAI_API_KEY не задан"
    try:
        return await extract_food_diary(transcript), None
    except ParseError as exc:
        return None, str(exc)


async def compare_from_text(
    text: str,
    kind: CompareKind = "consumption",
) -> CompareResult:
    recorded_at = datetime.now(MOSCOW)
    transcript = text.strip()
    preview_kind = "inventory" if kind == "inventory" else "consumption"
    parse = parse_consumption_text if kind == "consumption" else parse_inventory_text
    local_parsed = await parse(transcript)
    local_preview = preview_parsed(
        local_parsed, transcript, kind=preview_kind, recorded_at=recorded_at
    )

    openai_preview: PendingBatch | None = None
    food_diary: FoodDiaryResult | None = None
    openai_parse_error: str | None = None

    async with openai_vpn_session():
        if kind == "inventory":
            openai_preview, openai_parse_error = await _run_openai_inventory(transcript)
        else:
            food_diary, openai_parse_error = await _run_openai_diary(transcript)

    return CompareResult(
        kind=kind,
        local_stt=transcript,
        gigaam_stt=None,
        gigaam_stt_error=None,
        openai_stt=None,
        openai_stt_error=None,
        local_preview=local_preview,
        openai_preview=openai_preview,
        food_diary=food_diary,
        openai_parse_error=openai_parse_error,
        recorded_at=recorded_at,
    )


async def compare_from_voice(
    audio_bytes: bytes,
    filename: str = "voice.ogg",
    kind: CompareKind = "consumption",
) -> CompareResult:
    settings = get_settings()
    recorded_at = datetime.now(MOSCOW)

    # Sequential local STT to avoid RAM spike (Whisper then GigaAM, both unload).
    local_stt = await transcribe_audio(audio_bytes, filename=filename)

    gigaam_stt: str | None = None
    gigaam_stt_error: str | None = None
    if settings.gigaam_enabled:
        try:
            gigaam_stt = await transcribe_audio_gigaam(audio_bytes, filename=filename)
        except TranscriptionError as exc:
            gigaam_stt_error = str(exc)
    else:
        gigaam_stt_error = "GigaAM выключен"

    # Ollama (+ OpenAI inventory/diary parse) use GigaAM text when available.
    parse_transcript = gigaam_stt or local_stt

    openai_stt: str | None = None
    openai_stt_error: str | None = None
    openai_preview: PendingBatch | None = None
    food_diary: FoodDiaryResult | None = None
    openai_parse_error: str | None = None

    preview_kind = "inventory" if kind == "inventory" else "consumption"
    parse = parse_consumption_text if kind == "consumption" else parse_inventory_text
    local_task = asyncio.create_task(parse(parse_transcript))
    try:
        async with openai_vpn_session():
            if settings.has_openai:
                stt_task = asyncio.create_task(
                    transcribe_audio_openai(audio_bytes, filename=filename)
                )
                if kind == "inventory":
                    cloud_task = asyncio.create_task(
                        _run_openai_inventory(parse_transcript)
                    )
                else:
                    cloud_task = asyncio.create_task(_run_openai_diary(parse_transcript))
                stt_outcome, cloud_outcome = await asyncio.gather(
                    stt_task, cloud_task, return_exceptions=True
                )
                if isinstance(stt_outcome, Exception):
                    openai_stt_error = str(stt_outcome)
                else:
                    openai_stt = stt_outcome
                if isinstance(cloud_outcome, Exception):
                    openai_parse_error = str(cloud_outcome)
                elif kind == "inventory":
                    openai_preview, openai_parse_error = cloud_outcome
                else:
                    food_diary, openai_parse_error = cloud_outcome
            else:
                openai_stt_error = "OPENAI_API_KEY не задан"
                openai_parse_error = "OPENAI_API_KEY не задан"
    finally:
        local_parsed = await local_task

    if isinstance(local_parsed, Exception):
        raise local_parsed

    local_preview = preview_parsed(
        local_parsed, parse_transcript, kind=preview_kind, recorded_at=recorded_at
    )

    return CompareResult(
        kind=kind,
        local_stt=local_stt,
        gigaam_stt=gigaam_stt,
        gigaam_stt_error=gigaam_stt_error,
        openai_stt=openai_stt,
        openai_stt_error=openai_stt_error,
        local_preview=local_preview,
        openai_preview=openai_preview,
        food_diary=food_diary,
        openai_parse_error=openai_parse_error,
        recorded_at=recorded_at,
    )
