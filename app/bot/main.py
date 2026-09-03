import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.config import get_settings
from app.db.session import SessionLocal
from app.services.inventory import (
    cancel_batch,
    confirm_batch,
    format_inventory_list,
    format_pending_table,
    list_inventory,
)
from app.services.voice_pipeline import process_voice_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
bot: Bot | None = None
dp = Dispatcher()


def get_bot() -> Bot:
    global bot
    if bot is None:
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        bot = Bot(token=settings.telegram_bot_token)
    return bot


def is_allowed(user_id: int | None) -> bool:
    if user_id is None:
        return False
    allowed = settings.allowed_user_ids
    return not allowed or user_id in allowed


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    await message.answer(
        "Отправьте голосовое с перечнем продуктов.\n"
        "Команды:\n"
        "/inventory — подтверждённые записи"
    )


@dp.message(Command("inventory"))
async def cmd_inventory(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    with SessionLocal() as db:
        rows = list_inventory(db, status="confirmed")
        await message.answer(format_inventory_list(rows))


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        return
    if not message.voice:
        return

    status_msg = await message.answer("Обрабатываю голосовое...")
    try:
        buffer = await get_bot().download(message.voice)
        if buffer is None:
            raise RuntimeError("Failed to download voice message")

        with SessionLocal() as db:
            batch = await process_voice_message(
                db=db,
                audio_bytes=buffer.read(),
                filename="voice.ogg",
                telegram_message_id=str(message.message_id),
            )

        await status_msg.edit_text(
            format_pending_table(batch),
            reply_markup=_confirm_keyboard(batch.batch_id),
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.exception("Voice processing failed")
        await status_msg.edit_text(f"Ошибка обработки: {exc}")


def _confirm_keyboard(batch_id: str) -> Any:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data=f"confirm:{batch_id}"),
                InlineKeyboardButton(text="Отменить", callback_data=f"cancel:{batch_id}"),
            ]
        ]
    )


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    batch_id = (callback.data or "").split(":", 1)[1]
    with SessionLocal() as db:
        count = confirm_batch(db, batch_id)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Сохранено")
    if callback.message:
        await callback.message.edit_text(f"Запись подтверждена ({count} поз.).")


@dp.callback_query(F.data.startswith("cancel:"))
async def on_cancel(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    batch_id = (callback.data or "").split(":", 1)[1]
    with SessionLocal() as db:
        count = cancel_batch(db, batch_id)
    if count == 0:
        await callback.answer("Запись уже обработана", show_alert=True)
        return
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text("Запись отменена.")


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    await dp.start_polling(get_bot())


if __name__ == "__main__":
    asyncio.run(main())
