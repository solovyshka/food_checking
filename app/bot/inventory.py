"""Inventory Telegram bot — STT queue → Qwen → Google Sheets; merge/replace into DB."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.bot.common import is_allowed, make_bot
from app.config import get_settings
from app.db.session import SessionLocal
from app.services.google_sheets import (
    GoogleSheetsError,
    current_sheet_url,
    export_current_inventory,
    export_proposal,
    proposal_sheet_url,
    read_proposal,
)
from app.services.inventory import (
    KIND_LABELS,
    PendingBatch,
    import_inventory_from_rows,
    list_inventory,
    merge_inventory_with_proposal,
)
from app.services.reports import ReportFile, report_pending_batch
from app.services.transcripts import (
    TranscriptPreview,
    cancel_inventory_transcript,
    confirm_inventory_transcript,
    format_transcript_preview,
    list_queued_inventory_days,
    parse_inventory_day,
)
from app.services.voice_pipeline import process_voice_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
dp = Dispatcher()
_bot = None

BTN_LIST_STOCK = "Список наличия"
BTN_PARSE_TX = "Разобрать транскрибации"
BTN_ADD_PROPOSAL = "Добавить предложение"
BTN_FULL_UPDATE = "Обновить полностью"
ALL_BUTTONS = frozenset(
    {BTN_LIST_STOCK, BTN_PARSE_TX, BTN_ADD_PROPOSAL, BTN_FULL_UPDATE}
)


def get_bot():
    global _bot
    if _bot is None:
        _bot = make_bot(settings.telegram_bot_token)
    return _bot


def keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LIST_STOCK)],
            [KeyboardButton(text=BTN_PARSE_TX)],
            [KeyboardButton(text=BTN_ADD_PROPOSAL)],
            [KeyboardButton(text=BTN_FULL_UPDATE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def help_text() -> str:
    return (
        f"Бот *наличия* (*{KIND_LABELS['inventory']}*).\n\n"
        "Голос → подтверждаете текст → очередь.\n"
        f"*{BTN_PARSE_TX}* — Qwen → вкладка «Предложение» в Sheets.\n"
        f"*{BTN_ADD_PROPOSAL}* — новые строки + перезапись пересечений.\n"
        f"*{BTN_FULL_UPDATE}* — наличие = только «Предложение».\n\n"
        f"• *{BTN_LIST_STOCK}* — ссылка на вкладку наличия\n"
        f"• *{BTN_PARSE_TX}* — разбор → ссылка на предложение\n"
        f"• *{BTN_ADD_PROPOSAL}* — merge из предложения\n"
        f"• *{BTN_FULL_UPDATE}* — полная замена из предложения\n\n"
        "Калории / «съел» — в отдельном боте."
    )


def _transcript_keyboard(transcript_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"txconfirm:{transcript_id}",
                ),
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"txcancel:{transcript_id}",
                ),
            ]
        ]
    )


def _days_keyboard(days) -> InlineKeyboardMarkup:
    rows = []
    for day in days:
        label = f"{day.entry_date.strftime('%d.%m.%Y')} ({day.count})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"invparse:{day.entry_date.isoformat()}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_html_report(
    message: Message,
    report: ReportFile,
    caption: str,
    *,
    status_msg: Message | None = None,
) -> None:
    if status_msg is not None:
        try:
            await status_msg.delete()
        except Exception:
            pass
    doc = FSInputFile(report.path, filename=report.filename)
    await message.answer_document(document=doc, caption=caption)


def _sync_sheets_after_parse(batch: PendingBatch) -> str:
    with SessionLocal() as db:
        current = list_inventory(db, status="confirmed")
    export_proposal(
        batch.rows,
        transcript=batch.transcript,
        unknown_names=batch.unknown_names,
        missing_quantity=batch.missing_quantity,
        skipped=batch.skipped,
    )
    export_current_inventory(current)
    return proposal_sheet_url()


async def _send_parse_result(
    message: Message, batch: PendingBatch, status_msg: Message | None = None
) -> None:
    prop_url = ""
    sheets_err = ""
    try:
        prop_url = _sync_sheets_after_parse(batch)
    except GoogleSheetsError as exc:
        sheets_err = f"Sheets: {exc}"
        logger.warning("Sheets export failed: %s", exc)
    except Exception as exc:
        sheets_err = f"Sheets ошибка: {exc}"
        logger.exception("Sheets export failed")

    lines = [
        f"Разбор готов: {len(batch.rows)} поз.",
        "Правьте «Предложение», затем «Добавить предложение» или «Обновить полностью».",
    ]
    if prop_url:
        lines.append(f"Предложение:\n{prop_url}")
    if sheets_err:
        lines.append(sheets_err)
    if batch.unknown_names:
        lines.append("Нет в справочнике: " + ", ".join(batch.unknown_names))
    text = "\n".join(lines)

    report = report_pending_batch(batch)
    if report:
        await _send_html_report(
            message,
            report,
            caption=f"{text}\nHTML: {report.url}",
            status_msg=status_msg,
        )
        return
    if status_msg is not None:
        await status_msg.edit_text(text)
    else:
        await message.answer(text, reply_markup=keyboard())


async def _send_transcript_preview(
    message: Message,
    preview: TranscriptPreview,
    status_msg: Message | None = None,
) -> None:
    text = format_transcript_preview(preview)
    markup = _transcript_keyboard(preview.id)
    if status_msg is not None:
        await status_msg.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def _run_parse_day(
    message: Message,
    entry_date: date,
    status_msg: Message | None = None,
) -> None:
    label = entry_date.strftime("%d.%m.%Y")
    if status_msg is None:
        status_msg = await message.answer(f"Qwen разбирает день {label}...")
    else:
        await status_msg.edit_text(f"Qwen разбирает день {label}...")
    try:
        with SessionLocal() as db:
            batch = await parse_inventory_day(db, entry_date)
        if batch is None:
            await status_msg.edit_text(f"Нет неразобранных текстов за {label}.")
            return
        await _send_parse_result(message, batch, status_msg=status_msg)
    except Exception as exc:
        logger.exception("Inventory day parse failed")
        await status_msg.edit_text(f"Ошибка разбора: {exc}")


def _apply_from_proposal(*, mode: str) -> str:
    """mode: merge | replace. Returns status text for Telegram."""
    items = read_proposal()
    if not items:
        return "Во вкладке «Предложение» нет строк с продуктом, кол-вом и единицей."
    payload = [(i.product_name, i.quantity, i.unit) for i in items]
    with SessionLocal() as db:
        if mode == "merge":
            batch_id, written, added, updated = merge_inventory_with_proposal(
                db, payload, source="sheets_merge"
            )
            detail = f"добавлено {added}, обновлено {updated}, всего {len(written)}"
        else:
            batch_id, written = import_inventory_from_rows(
                db, payload, source="sheets_replace"
            )
            detail = f"полностью {len(written)} поз. (только из предложения)"
        current = list_inventory(db, status="confirmed")
    try:
        export_current_inventory(current)
        url = current_sheet_url()
    except Exception:
        logger.exception("Sheets refresh after import failed")
        url = ""
    day = written[0].entry_date.strftime("%d.%m.%Y")
    lines = [
        f"Наличие на {day}: {detail}.",
        f"batch={batch_id[:8]}…",
    ]
    if url:
        lines.append(f"Наличие:\n{url}")
    return "\n".join(lines)


@dp.message(Command("start", "help", "mode"))
async def cmd_start(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    await message.answer(
        help_text(),
        reply_markup=keyboard(),
        parse_mode="Markdown",
    )


@dp.message(Command("inventory", "stock"))
@dp.message(F.text == BTN_LIST_STOCK)
async def cmd_inventory(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    with SessionLocal() as db:
        rows = list_inventory(db, status="confirmed")
    try:
        if rows:
            export_current_inventory(rows)
        url = current_sheet_url()
        if not rows:
            await message.answer(
                "Нет подтверждённого наличия в БД.\n"
                + (f"Таблица:\n{url}" if url else "Sheets не настроен."),
                reply_markup=keyboard(),
            )
            return
        day = rows[0].entry_date.strftime("%d.%m.%Y")
        await message.answer(
            f"Наличие на {day}: {len(rows)} поз.\n{url}",
            reply_markup=keyboard(),
        )
    except GoogleSheetsError as exc:
        await message.answer(f"Sheets: {exc}", reply_markup=keyboard())
    except Exception as exc:
        logger.exception("List stock / Sheets failed")
        await message.answer(f"Ошибка: {exc}", reply_markup=keyboard())


@dp.message(F.text == BTN_ADD_PROPOSAL)
async def cmd_add_proposal(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    status = await message.answer("Merge из «Предложение»...")
    try:
        text = _apply_from_proposal(mode="merge")
        await status.edit_text(text)
    except GoogleSheetsError as exc:
        await status.edit_text(f"Sheets: {exc}")
    except ValueError as exc:
        await status.edit_text(f"Не удалось импортировать: {exc}")
    except Exception as exc:
        logger.exception("Merge from Sheets failed")
        await status.edit_text(f"Ошибка: {exc}")


@dp.message(F.text == BTN_FULL_UPDATE)
async def cmd_full_update(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    status = await message.answer("Полная замена из «Предложение»...")
    try:
        text = _apply_from_proposal(mode="replace")
        await status.edit_text(text)
    except GoogleSheetsError as exc:
        await status.edit_text(f"Sheets: {exc}")
    except ValueError as exc:
        await status.edit_text(f"Не удалось импортировать: {exc}")
    except Exception as exc:
        logger.exception("Full replace from Sheets failed")
        await status.edit_text(f"Ошибка: {exc}")


@dp.message(F.text == BTN_PARSE_TX)
async def on_parse_transcripts(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    with SessionLocal() as db:
        days = list_queued_inventory_days(db)
    if not days:
        await message.answer(
            "Нечего разбирать — нет подтверждённых текстов в очереди.",
            reply_markup=keyboard(),
        )
        return
    if len(days) == 1:
        await _run_parse_day(message, days[0].entry_date)
        return
    lines = ["Выберите день для разбора:"]
    for day in days:
        lines.append(f"• {day.entry_date.strftime('%d.%m.%Y')} ({day.count})")
    await message.answer(
        "\n".join(lines),
        reply_markup=_days_keyboard(days),
    )


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    if not message.voice or not message.from_user:
        return

    status_msg = await message.answer("Распознаю голосовое (Наличие)...")
    try:
        t0 = time.perf_counter()
        buffer = await get_bot().download(message.voice)
        download_s = time.perf_counter() - t0
        if buffer is None:
            raise RuntimeError("Failed to download voice message")

        with SessionLocal() as db:
            result = await process_voice_message(
                db=db,
                audio_bytes=buffer.read(),
                filename="voice.ogg",
                telegram_message_id=str(message.message_id),
                kind="inventory",
            )
        voice_sec = message.voice.duration
        extra = f" · скачать {download_s:.1f}с"
        if voice_sec:
            extra += f" · аудио {voice_sec}с"
        if result.timing_note:
            result.timing_note += extra
        logger.info(
            "TIMING telegram_download=%.2fs voice_duration=%s",
            download_s,
            voice_sec,
        )
        if isinstance(result, TranscriptPreview):
            await _send_transcript_preview(message, result, status_msg=status_msg)
        else:
            await _send_parse_result(message, result, status_msg=status_msg)
    except Exception as exc:
        logger.exception("Voice processing failed")
        await status_msg.edit_text(f"Ошибка обработки: {exc}")


@dp.message(F.text)
async def handle_text(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    if not message.text or message.text.strip() in ALL_BUTTONS:
        return
    await message.answer(
        "Этот бот — только *наличие* (голос).\n"
        "Калории / «съел» — в боте потребления.",
        reply_markup=keyboard(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data.startswith("txconfirm:"))
async def on_tx_confirm(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    raw = (callback.data or "").split(":", 1)
    if len(raw) != 2 or not raw[1].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return
    transcript_id = int(raw[1])
    with SessionLocal() as db:
        preview = confirm_inventory_transcript(db, transcript_id)
    if preview is None:
        await callback.answer("Уже обработано", show_alert=True)
        return
    await callback.answer("В очереди")
    day = preview.entry_date.strftime("%d.%m.%Y")
    if callback.message:
        await callback.message.edit_text(
            f"Текст в очереди на разбор.\n"
            f"День: {day}\n"
            f"«{preview.text}»"
        )


@dp.callback_query(F.data.startswith("txcancel:"))
async def on_tx_cancel(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    raw = (callback.data or "").split(":", 1)
    if len(raw) != 2 or not raw[1].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return
    transcript_id = int(raw[1])
    with SessionLocal() as db:
        ok = cancel_inventory_transcript(db, transcript_id)
    if not ok:
        await callback.answer("Уже обработано", show_alert=True)
        return
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text("Текст отклонён, в очередь не попал.")


@dp.callback_query(F.data.startswith("invparse:"))
async def on_parse_day(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":", 1)
    if len(parts) != 2:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    try:
        entry_date = date.fromisoformat(parts[1])
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await _run_parse_day(
            callback.message,
            entry_date,
            status_msg=callback.message,
        )


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    await dp.start_polling(get_bot())


if __name__ == "__main__":
    asyncio.run(main())
