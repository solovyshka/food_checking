"""Consumption / calories Telegram bot — «съел» with deferred Qwen parse."""

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
    list_consumption,
)
from app.services.reports import ReportFile, report_entries_list, report_pending_batch
from app.services.transcripts import (
    TranscriptPreview,
    cancel_transcript,
    confirm_transcript,
    finalize_parse,
    format_period,
    format_transcript_preview,
    list_queued_periods,
    parse_period,
)
from app.services.voice_pipeline import process_text_message, process_voice_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
dp = Dispatcher()
_bot = None

BTN_LIST_EAT = "Список съеденного"
BTN_PARSE_TX = "Разобрать транскрибации"
ALL_BUTTONS = frozenset({BTN_LIST_EAT, BTN_PARSE_TX})


def get_bot():
    global _bot
    if _bot is None:
        token = settings.telegram_consumption_bot_token or settings.telegram_bot_token
        _bot = make_bot(token)
    return _bot


def keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LIST_EAT)],
            [KeyboardButton(text=BTN_PARSE_TX)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def help_text() -> str:
    return (
        f"Бот *потребления калорий* (*{KIND_LABELS['consumption']}*).\n\n"
        "Голос или *текст* → подтверждаете распознанный текст.\n"
        "Тексты копятся по периоду (дата + завтрак/обед/ужин/перекус).\n"
        f"*{BTN_PARSE_TX}* — Qwen разбирает все тексты выбранного периода.\n\n"
        f"• *{BTN_LIST_EAT}* — список съеденного\n"
        f"• *{BTN_PARSE_TX}* — разобрать очередь\n\n"
        "Наличие продуктов — в отдельном боте."
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
                    callback_data=f"confirm:eat:{batch_id}",
                ),
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"cancel:eat:{batch_id}",
                ),
            ]
        ]
    )


def _periods_keyboard(periods) -> InlineKeyboardMarkup:
    rows = []
    for period in periods:
        label = (
            f"{period.entry_date.strftime('%d.%m')} · "
            f"{period.meal_type} ({period.count})"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=(
                        f"parse:{period.entry_date.isoformat()}:{period.meal_type}"
                    ),
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
            f"Режим: {KIND_LABELS[batch.kind]}\n"
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


async def _run_parse_period(
    message: Message,
    entry_date: date,
    meal_type: str,
    status_msg: Message | None = None,
) -> None:
    label = format_period(entry_date, meal_type)
    if status_msg is None:
        status_msg = await message.answer(f"Qwen разбирает период {label}...")
    else:
        await status_msg.edit_text(f"Qwen разбирает период {label}...")
    try:
        with SessionLocal() as db:
            batch = await parse_period(db, entry_date, meal_type)
        if batch is None:
            await status_msg.edit_text(f"Нет неразобранных текстов за {label}.")
            return
        await _send_pending(message, batch, status_msg=status_msg)
    except Exception as exc:
        logger.exception("Period parse failed")
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


@dp.message(Command("eat", "consumption"))
@dp.message(F.text == BTN_LIST_EAT)
async def cmd_consumption(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    with SessionLocal() as db:
        rows = list_consumption(db, status="confirmed")
    if not rows:
        await message.answer(
            format_entries_list(rows, kind="consumption"),
            reply_markup=keyboard(),
        )
        return
    report = report_entries_list(rows, kind="consumption")
    if report:
        await _send_html_report(
            message,
            report,
            caption=f"Съел: {len(rows)} записей.\n{report.url}",
        )
        return
    await message.answer(
        format_entries_list(rows, kind="consumption"),
        reply_markup=keyboard(),
    )


@dp.message(F.text == BTN_PARSE_TX)
async def on_parse_transcripts(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    with SessionLocal() as db:
        periods = list_queued_periods(db)
    if not periods:
        await message.answer(
            "Нечего разбирать — нет подтверждённых текстов в очереди.",
            reply_markup=keyboard(),
        )
        return
    if len(periods) == 1:
        period = periods[0]
        await _run_parse_period(message, period.entry_date, period.meal_type)
        return
    lines = ["Выберите период для разбора:"]
    for period in periods:
        lines.append(
            f"• {format_period(period.entry_date, period.meal_type)} "
            f"({period.count})"
        )
    await message.answer(
        "\n".join(lines),
        reply_markup=_periods_keyboard(periods),
    )


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    if not message.voice or not message.from_user:
        return

    status_msg = await message.answer("Распознаю голосовое...")
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
                kind="consumption",
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
    if not message.from_user or not message.text:
        return
    text = message.text.strip()
    if not text or text in ALL_BUTTONS:
        return

    status_msg = await message.answer("Сохраняю текст...")
    try:
        with SessionLocal() as db:
            result = await process_text_message(
                db=db,
                text=text,
                telegram_message_id=str(message.message_id),
                kind="consumption",
            )
        if isinstance(result, TranscriptPreview):
            await _send_transcript_preview(message, result, status_msg=status_msg)
        else:
            await _send_pending(message, result, status_msg=status_msg)
    except Exception as exc:
        logger.exception("Text processing failed")
        await status_msg.edit_text(f"Ошибка обработки: {exc}")


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
        preview = confirm_transcript(db, transcript_id)
    if preview is None:
        await callback.answer("Уже обработано", show_alert=True)
        return
    await callback.answer("В очереди")
    period = format_period(preview.entry_date, preview.meal_type)
    if callback.message:
        await callback.message.edit_text(
            f"Текст в очереди на разбор.\n"
            f"Период: {period}\n"
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
        ok = cancel_transcript(db, transcript_id)
    if not ok:
        await callback.answer("Уже обработано", show_alert=True)
        return
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text("Текст отклонён, в очередь не попал.")


@dp.callback_query(F.data.startswith("parse:"))
async def on_parse_period(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    _, date_s, meal_type = parts
    try:
        entry_date = date.fromisoformat(date_s)
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await _run_parse_period(
            callback.message,
            entry_date,
            meal_type,
            status_msg=callback.message,
        )


@dp.callback_query(F.data.startswith("confirm:eat:"))
async def on_confirm(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    batch_id = (callback.data or "").split(":", 2)[-1]
    with SessionLocal() as db:
        count = finalize_parse(db, batch_id, confirm=True)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Сохранено")
    if callback.message:
        await callback.message.edit_text(
            f"{KIND_LABELS['consumption']}: подтверждено ({count} поз.)."
        )


@dp.callback_query(F.data.startswith("cancel:eat:"))
async def on_cancel(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    batch_id = (callback.data or "").split(":", 2)[-1]
    with SessionLocal() as db:
        count = finalize_parse(db, batch_id, confirm=False)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text(
            f"{KIND_LABELS['consumption']}: разбор отменён, тексты снова в очереди."
        )


async def main() -> None:
    token = settings.telegram_consumption_bot_token or settings.telegram_bot_token
    if not token:
        raise RuntimeError(
            "TELEGRAM_CONSUMPTION_BOT_TOKEN (or TELEGRAM_BOT_TOKEN) is not set"
        )
    await dp.start_polling(get_bot())


if __name__ == "__main__":
    asyncio.run(main())
