"""Inventory Telegram bot — deferred STT confirm, then batch Qwen parse."""

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
from app.services.inventory import (
    KIND_LABELS,
    PendingBatch,
    format_entries_list,
    format_pending_table,
    list_inventory,
)
from app.services.reports import ReportFile, report_entries_list, report_pending_batch
from app.services.transcripts import (
    TranscriptPreview,
    cancel_inventory_transcript,
    confirm_inventory_transcript,
    finalize_inventory_parse,
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
ALL_BUTTONS = frozenset({BTN_LIST_STOCK, BTN_PARSE_TX})


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
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def help_text() -> str:
    return (
        f"Бот *наличия* (*{KIND_LABELS['inventory']}*).\n\n"
        "Голос → подтверждаете распознанный текст.\n"
        "Тексты копятся по дню; "
        f"*{BTN_PARSE_TX}* — Qwen разбирает все тексты выбранного дня.\n\n"
        f"• *{BTN_LIST_STOCK}* — список наличия\n"
        f"• *{BTN_PARSE_TX}* — разобрать очередь\n\n"
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


def _confirm_keyboard(batch_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"confirm:inv:{batch_id}",
                ),
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"cancel:inv:{batch_id}",
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
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if status_msg is not None:
        try:
            await status_msg.delete()
        except Exception:
            pass
    doc = FSInputFile(report.path, filename=report.filename)
    await message.answer_document(
        document=doc,
        caption=caption,
        reply_markup=reply_markup,
    )


async def _send_pending(
    message: Message, batch: PendingBatch, status_msg: Message | None = None
) -> None:
    report = report_pending_batch(batch)
    markup = _confirm_keyboard(batch.batch_id) if batch.batch_id else None
    if report:
        caption = (
            f"Режим: {KIND_LABELS['inventory']}\n"
            f"Позиций: {len(batch.rows)}\n"
            f"Открой файл или ссылку:\n{report.url}"
        )
        await _send_html_report(
            message,
            report,
            caption,
            status_msg=status_msg,
            reply_markup=markup,
        )
        return
    text = format_pending_table(batch)
    if status_msg is not None:
        await status_msg.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


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
        await _send_pending(message, batch, status_msg=status_msg)
    except Exception as exc:
        logger.exception("Inventory day parse failed")
        await status_msg.edit_text(f"Ошибка разбора: {exc}")


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
    if not rows:
        await message.answer(
            format_entries_list(rows, kind="inventory"),
            reply_markup=keyboard(),
        )
        return
    report = report_entries_list(rows, kind="inventory")
    if report:
        await _send_html_report(
            message,
            report,
            caption=f"Наличие: {len(rows)} записей.\n{report.url}",
        )
        return
    await message.answer(
        format_entries_list(rows, kind="inventory"),
        reply_markup=keyboard(),
    )


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
        lines.append(
            f"• {day.entry_date.strftime('%d.%m.%Y')} ({day.count})"
        )
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
            await _send_pending(message, result, status_msg=status_msg)
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


@dp.callback_query(F.data.startswith("confirm:inv:"))
async def on_confirm(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    batch_id = (callback.data or "").split(":", 2)[-1]
    with SessionLocal() as db:
        count = finalize_inventory_parse(db, batch_id, confirm=True)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Сохранено")
    if callback.message:
        try:
            await callback.message.edit_caption(
                caption=f"{KIND_LABELS['inventory']}: подтверждено ({count} поз.)."
            )
        except Exception:
            await callback.message.edit_text(
                f"{KIND_LABELS['inventory']}: подтверждено ({count} поз.)."
            )


@dp.callback_query(F.data.startswith("cancel:inv:"))
async def on_cancel(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    batch_id = (callback.data or "").split(":", 2)[-1]
    with SessionLocal() as db:
        count = finalize_inventory_parse(db, batch_id, confirm=False)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Отменено")
    if callback.message:
        text = f"{KIND_LABELS['inventory']}: разбор отменён, тексты снова в очереди."
        try:
            await callback.message.edit_caption(caption=text)
        except Exception:
            await callback.message.edit_text(text)


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    await dp.start_polling(get_bot())


if __name__ == "__main__":
    asyncio.run(main())
