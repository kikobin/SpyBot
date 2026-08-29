import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    Message,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from db import (
    init_db,
    save_connection,
    get_user_chat_id,
    save_message,
    get_message,
    find_by_message_id,
    delete_cached,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sender(msg: Message) -> str:
    u = msg.from_user
    if not u:
        return msg.chat.title or "Unknown"
    name = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
    return name.strip() or u.username or str(u.id)


def _extract_media(msg: Message) -> tuple[Optional[str], Optional[str]]:
    """Returns (media_type, file_id) or (None, None)."""
    if msg.photo:
        return "photo", msg.photo[-1].file_id  # largest size
    if msg.video:
        return "video", msg.video.file_id
    if msg.voice:
        return "voice", msg.voice.file_id
    if msg.video_note:
        return "video_note", msg.video_note.file_id
    if msg.audio:
        return "audio", msg.audio.file_id
    if msg.document:
        return "document", msg.document.file_id
    if msg.sticker:
        return "sticker", msg.sticker.file_id
    if msg.animation:
        return "animation", msg.animation.file_id
    return None, None


async def _send_media(bot: Bot, chat_id: int, mtype: str, file_id: str, caption: str = ""):
    try:
        match mtype:
            case "photo":       await bot.send_photo(chat_id, file_id, caption=caption)
            case "video":       await bot.send_video(chat_id, file_id, caption=caption)
            case "voice":       await bot.send_voice(chat_id, file_id)
            case "video_note":  await bot.send_video_note(chat_id, file_id)
            case "audio":       await bot.send_audio(chat_id, file_id, caption=caption)
            case "document":    await bot.send_document(chat_id, file_id, caption=caption)
            case "sticker":     await bot.send_sticker(chat_id, file_id)
            case "animation":   await bot.send_animation(chat_id, file_id, caption=caption)
    except Exception as e:
        logger.warning("send_media(%s) failed: %s", mtype, e)


# ── Handlers ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def on_start(message: Message, bot: Bot):
    await message.answer(
        "👋 <b>Dialog Spy Bot</b>\n\n"
        "Этот бот отслеживает удаление и редактирование сообщений в твоих чатах.\n\n"
        "<b>Как подключить:</b>\n"
        "1. Убедись что у тебя <b>Telegram Business</b> или <b>Telegram Premium</b>\n"
        "2. Настройки → Telegram Business → Чат-боты\n"
        f"3. Введи username этого бота\n\n"
        "<b>Что умеет:</b>\n"
        "🔔 Уведомление если собеседник удалил сообщение — с текстом и медиа\n"
        "✏️ Уведомление если собеседник изменил сообщение — было/стало\n"
        "📸 Кэширует все медиафайлы автоматически",
        parse_mode=ParseMode.HTML,
    )


@router.business_connection()
async def on_business_connection(bc: BusinessConnection, bot: Bot):
    await save_connection(bc.id, bc.user_chat_id, bc.user.id, bc.is_enabled)

    if bc.is_enabled:
        await bot.send_message(
            bc.user_chat_id,
            "✅ <b>Бот подключён!</b>\n\n"
            "Теперь я:\n"
            "🔔 Пришлю уведомление если собеседник удалит или изменит сообщение\n"
            "📸 Сохраню фото/видео/голосовые автоматически\n\n"
            "<i>Работает только с новыми сообщениями после подключения.</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await bot.send_message(bc.user_chat_id, "❌ Бот отключён от вашего аккаунта.")


@router.business_message()
async def on_business_message(message: Message, bot: Bot):
    """Cache every incoming business message."""
    conn_id = message.business_connection_id
    if not conn_id:
        return

    mtype, file_id = _extract_media(message)
    text = message.text or message.caption or ""
    date = int(message.date.timestamp()) if message.date else 0

    await save_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_name=_sender(message),
        text=text,
        date=date,
        media_type=mtype,
        file_id=file_id,
    )


@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot):
    """Notify owner when their interlocutor edits a message."""
    conn_id = message.business_connection_id
    if not conn_id:
        return

    user_chat_id = await get_user_chat_id(conn_id)
    if not user_chat_id:
        return

    cached = await get_message(conn_id, message.chat.id, message.message_id)
    old_text = cached["text"] if cached else "<i>(не сохранено)</i>"
    new_text = message.text or message.caption or ""
    sender = _sender(message)
    chat_label = message.chat.title or sender

    await bot.send_message(
        user_chat_id,
        f"✏️ <b>Сообщение изменено</b>\n\n"
        f"👤 {sender}  |  💬 {chat_label}\n\n"
        f"<b>Было:</b>\n{old_text}\n\n"
        f"<b>Стало:</b>\n{new_text}",
        parse_mode=ParseMode.HTML,
    )

    # Update cache with new text
    mtype, file_id = _extract_media(message)
    await save_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_name=sender,
        text=new_text,
        date=int(message.date.timestamp()) if message.date else 0,
        media_type=mtype,
        file_id=file_id,
    )


@router.deleted_business_messages()
async def on_deleted_business_messages(event: BusinessMessagesDeleted, bot: Bot):
    """Notify owner when messages are deleted, include cached content."""
    conn_id = event.business_connection_id
    user_chat_id = await get_user_chat_id(conn_id)
    if not user_chat_id:
        return

    chat_id = event.chat.id if event.chat else None

    for msg_id in event.message_ids:
        if chat_id:
            cached = await get_message(conn_id, chat_id, msg_id)
        else:
            results = await find_by_message_id(conn_id, msg_id)
            cached = results[0] if results else None
            if cached:
                chat_id = cached["chat_id"]

        if not cached:
            continue

        sender = cached["sender_name"] or "Unknown"
        text = cached["text"] or ""
        mtype = cached["media_type"]
        file_id = cached["file_id"]

        parts = [f"🗑 <b>Сообщение удалено</b>\n\n👤 {sender}"]
        if text:
            parts.append(f"\n💬 {text}")
        if mtype:
            parts.append(f"\n📎 <i>{mtype}</i>")

        await bot.send_message(user_chat_id, "".join(parts), parse_mode=ParseMode.HTML)

        if mtype and file_id:
            await _send_media(bot, user_chat_id, mtype, file_id, caption=f"🗑 Удалённый {mtype}")

        await delete_cached(conn_id, chat_id, msg_id)


# ── Start ─────────────────────────────────────────────────────────────────────

async def main():
    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    logger.info("Запущен: @%s", me.username)

    await dp.start_polling(bot, allowed_updates=[
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
        "message",  # for /start
    ])


if __name__ == "__main__":
    asyncio.run(main())
