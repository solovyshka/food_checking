import asyncio
import logging
import socket
import time
from typing import Any, Literal

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.config import get_settings
from app.db.session import SessionLocal
from app.services.inventory import (
    EntryKind,
    KIND_LABELS,
    cancel_batch,
    confirm_batch,
    format_entries_list,
    format_pending_table,
    list_consumption,
    list_inventory,
)
from app.services.voice_pipeline import process_text_message, process_voice_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
bot: Bot | None = None
dp = Dispatcher()

BotMode = Literal["inventory", "consumption"]

BTN_STOCK = "Наличие"
BTN_EAT = "Съел"
BTN_LIST_STOCK = "Список наличия"
BTN_LIST_EAT = "Список съеденного"

ALL_BUTTONS = frozenset(
    {
        BTN_STOCK,
        BTN_EAT,
        BTN_LIST_STOCK,
        BTN_LIST_EAT,
    }
)

_user_modes: dict[int, BotMode] = {}


def get_bot() -> Bot:
    global bot
    if bot is None:
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        # api.telegram.org has broken IPv6 from this host; happy-eyeballs then times out.
        session = AiohttpSession()
        session._connector_init["family"] = socket.AF_INET
        bot = Bot(token=settings.telegram_bot_token, session=session)
    return bot


def is_allowed(user_id: int | None) -> bool:
    if user_id is None:
        return False
    allowed = settings.allowed_user_ids
    return not allowed or user_id in allowed


def get_mode(user_id: int) -> BotMode:
    return _user_modes.get(user_id, "inventory")


def set_mode(user_id: int, kind: BotMode) -> None:
    _user_modes[user_id] = kind


def mode_keyboard(kind: BotMode) -> ReplyKeyboardMarkup:
    if kind == "consumption":
        rows = [
            [KeyboardButton(text=BTN_STOCK)],
            [KeyboardButton(text=BTN_LIST_EAT)],
        ]
    else:
        rows = [
            [KeyboardButton(text=BTN_EAT)],
            [KeyboardButton(text=BTN_LIST_STOCK)],
        ]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def _help_text(kind: BotMode) -> str:
    if kind == "consumption":
        return (
            f"Режим: *{KIND_LABELS[kind]}*\n\n"
            "Голос или *текст*. Пишем все распознанные блюда, "
            "единицы — *г* или *мл* (порции переводятся). "
            "Калорийность — если скажете число («100 калорий»), пишем как ккал/100 г.\n"
            f"• *{BTN_STOCK}* — наличие\n"
            f"• *{BTN_LIST_EAT}* — список съеденного"
        )
    return (
        f"Режим: *{KIND_LABELS[kind]}*\n\n"
        "Только *голосовое* — что есть дома.\n"
        f"• *{BTN_EAT}* — съел (голос/текст)\n"
        f"• *{BTN_LIST_STOCK}* — список наличия"
    )


async def _send_pending(message: Message, batch: Any, status_msg: Message | None = None) -> None:
    text = format_pending_table(batch)
    markup = (
        _confirm_keyboard(batch.kind, batch.batch_id) if batch.batch_id else None
    )
    if status_msg is not None:
        await status_msg.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await message.answer(text, reply_markup=markup, parse_mode="Markdown")


@dp.message(Command("start", "help", "mode"))
async def cmd_start(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    user_id = message.from_user.id  # type: ignore[union-attr]
    kind = get_mode(user_id)
    await message.answer(
        _help_text(kind),
        reply_markup=mode_keyboard(kind),
        parse_mode="Markdown",
    )


@dp.message(Command("inventory", "stock"))
async def cmd_inventory(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    user_id = message.from_user.id  # type: ignore[union-attr]
    kind = get_mode(user_id)
    with SessionLocal() as db:
        rows = list_inventory(db, status="confirmed")
        await message.answer(
            format_entries_list(rows, kind="inventory"),
            reply_markup=mode_keyboard(kind),
        )


@dp.message(Command("eat", "consumption"))
async def cmd_consumption(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    user_id = message.from_user.id  # type: ignore[union-attr]
    kind = get_mode(user_id)
    with SessionLocal() as db:
        rows = list_consumption(db, status="confirmed")
        await message.answer(
            format_entries_list(rows, kind="consumption"),
            reply_markup=mode_keyboard(kind),
        )


@dp.message(F.text == BTN_STOCK)
async def on_mode_stock(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    user_id = message.from_user.id  # type: ignore[union-attr]
    set_mode(user_id, "inventory")
    await message.answer(
        _help_text("inventory"),
        reply_markup=mode_keyboard("inventory"),
        parse_mode="Markdown",
    )


@dp.message(F.text == BTN_EAT)
async def on_mode_eat(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    user_id = message.from_user.id  # type: ignore[union-attr]
    set_mode(user_id, "consumption")
    await message.answer(
        _help_text("consumption"),
        reply_markup=mode_keyboard("consumption"),
        parse_mode="Markdown",
    )


@dp.message(F.text == BTN_LIST_STOCK)
async def on_list_stock(message: Message) -> None:
    await cmd_inventory(message)


@dp.message(F.text == BTN_LIST_EAT)
async def on_list_eat(message: Message) -> None:
    await cmd_consumption(message)


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    if not message.voice or not message.from_user:
        return

    mode = get_mode(message.from_user.id)
    label = KIND_LABELS[mode]
    status_msg = await message.answer(f"Обрабатываю голосовое ({label})...")
    try:
        t0 = time.perf_counter()
        buffer = await get_bot().download(message.voice)
        download_s = time.perf_counter() - t0
        if buffer is None:
            raise RuntimeError("Failed to download voice message")

        with SessionLocal() as db:
            batch = await process_voice_message(
                db=db,
                audio_bytes=buffer.read(),
                filename="voice.ogg",
                telegram_message_id=str(message.message_id),
                kind=mode,
            )
        voice_sec = message.voice.duration
        extra = f" · скачать {download_s:.1f}с"
        if voice_sec:
            extra += f" · аудио {voice_sec}с"
        if batch.timing_note:
            batch.timing_note += extra
        logger.info(
            "TIMING telegram_download=%.2fs voice_duration=%s",
            download_s,
            voice_sec,
        )

        await _send_pending(message, batch, status_msg=status_msg)
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

    mode = get_mode(message.from_user.id)
    if mode != "consumption":
        await message.answer(
            "Текст — в режиме *Съел*.\n"
            f"Нажмите *{BTN_EAT}*.",
            reply_markup=mode_keyboard(mode),
            parse_mode="Markdown",
        )
        return

    status_msg = await message.answer("Обрабатываю текст (Съел)...")
    try:
        with SessionLocal() as db:
            batch = await process_text_message(
                db=db,
                text=text,
                telegram_message_id=str(message.message_id),
                kind="consumption",
            )
        await _send_pending(message, batch, status_msg=status_msg)
    except Exception as exc:
        logger.exception("Text processing failed")
        await status_msg.edit_text(f"Ошибка обработки: {exc}")


def _confirm_keyboard(kind: EntryKind, batch_id: str) -> Any:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    prefix = "inv" if kind == "inventory" else "eat"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"confirm:{prefix}:{batch_id}",
                ),
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"cancel:{prefix}:{batch_id}",
                ),
            ]
        ]
    )


def _parse_kind(prefix: str) -> EntryKind | None:
    if prefix == "inv":
        return "inventory"
    if prefix == "eat":
        return "consumption"
    return None


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    _, prefix, batch_id = parts
    kind = _parse_kind(prefix)
    if kind is None:
        await callback.answer("Неизвестный режим", show_alert=True)
        return
    with SessionLocal() as db:
        count = confirm_batch(db, batch_id, kind=kind)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Сохранено")
    label = KIND_LABELS[kind]
    if callback.message:
        await callback.message.edit_text(f"{label}: подтверждено ({count} поз.).")


@dp.callback_query(F.data.startswith("cancel:"))
async def on_cancel(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    _, prefix, batch_id = parts
    kind = _parse_kind(prefix)
    if kind is None:
        await callback.answer("Неизвестный режим", show_alert=True)
        return
    with SessionLocal() as db:
        count = cancel_batch(db, batch_id, kind=kind)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Отменено")
    label = KIND_LABELS[kind]
    if callback.message:
        await callback.message.edit_text(f"{label}: запись отменена.")


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    await dp.start_polling(get_bot())


if __name__ == "__main__":
    asyncio.run(main())
