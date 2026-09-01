import asyncio
import io
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, Router
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
    MAX_MEDIA_BYTES,
    init_db,
    save_connection,
    get_connection,
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

def _sender_name(msg: Message) -> str:
    u = msg.from_user
    if not u:
        return msg.chat.title or "Unknown"
    name = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
    return name.strip() or u.username or str(u.id)


def _extract_file_id(msg: Message) -> tuple[Optional[str], Optional[str]]:
    """Returns (media_type, file_id). Largest photo size used."""
    if msg.photo:
        return "photo", msg.photo[-1].file_id
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


async def _download_media(bot: Bot, file_id: str) -> Optional[bytes]:
    """Download file by file_id, return raw bytes (or None if too large / error)."""
    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_size and tg_file.file_size > MAX_MEDIA_BYTES:
            logger.info("skip download: %s bytes > limit", tg_file.file_size)
            return None
        buf = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=buf)
        return buf.getvalue()
    except Exception as e:
        logger.warning("download_media failed: %s", e)
        return None


async def _send_cached_media(bot: Bot, chat_id: int, cached: dict):
    """Re-send media from cache using raw bytes (primary) or file_id (fallback)."""
    mtype = cached.get("media_type")
    if not mtype:
        return

    raw: Optional[bytes] = cached.get("media_data")
    file_id: Optional[str] = cached.get("file_id")

    async def send_buf(data: bytes):
        ext = {"video": "mp4", "voice": "ogg", "audio": "mp3",
               "video_note": "mp4", "document": "bin"}.get(mtype, "jpg")
        buf = io.BytesIO(data)
        buf.name = f"media.{ext}"
        cap = f"🗑 Удалённый {mtype}"
        try:
            match mtype:
                case "photo":      await bot.send_photo(chat_id, buf, caption=cap)
                case "video":      await bot.send_video(chat_id, buf, caption=cap)
                case "voice":      await bot.send_voice(chat_id, buf)
                case "video_note": await bot.send_video_note(chat_id, buf)
                case "audio":      await bot.send_audio(chat_id, buf, caption=cap)
                case _:            await bot.send_document(chat_id, buf, caption=cap)
        except Exception as e:
            logger.warning("send_buf(%s) failed: %s", mtype, e)

    async def send_fid(fid: str):
        cap = f"🗑 Удалённый {mtype}"
        try:
            match mtype:
                case "photo":      await bot.send_photo(chat_id, fid, caption=cap)
                case "video":      await bot.send_video(chat_id, fid, caption=cap)
                case "voice":      await bot.send_voice(chat_id, fid)
                case "video_note": await bot.send_video_note(chat_id, fid)
                case "audio":      await bot.send_audio(chat_id, fid, caption=cap)
                case "sticker":    await bot.send_sticker(chat_id, fid)
                case "animation":  await bot.send_animation(chat_id, fid, caption=cap)
                case _:            await bot.send_document(chat_id, fid, caption=cap)
        except Exception as e:
            logger.warning("send_fid(%s) failed: %s", mtype, e)

    if raw:
        await send_buf(raw)
    elif file_id:
        await send_fid(file_id)


# ── Handlers ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def on_start(message: Message):
    await message.answer(
        "👋 <b>Dialog Spy Bot</b>\n\n"
        "Бот отслеживает удаление и редактирование сообщений в твоих чатах.\n\n"
        "<b>Как подключить:</b>\n"
        "1. Нужна подписка <b>Telegram Business</b>\n"
        "2. Настройки → Telegram Business → Чат-боты\n"
        "3. Введи username этого бота\n\n"
        "<b>Что умеет:</b>\n"
        "🗑 Сообщение удалено → пришлю копию с медиа\n"
        "✏️ Сообщение изменено → покажу было/стало\n"
        "📸 Всё входящее медиа кэшируется автоматически",
        parse_mode=ParseMode.HTML,
    )


@router.business_connection()
async def on_business_connection(bc: BusinessConnection, bot: Bot):
    await save_connection(bc.id, bc.user_chat_id, bc.user.id, bc.is_enabled)
    if bc.is_enabled:
        await bot.send_message(
            bc.user_chat_id,
            "✅ <b>Бот подключён!</b>\n\n"
            "🗑 Пришлю копию если собеседник удалит сообщение\n"
            "✏️ Пришлю было/стало если собеседник изменит сообщение\n\n"
            "<i>Работает только с новыми сообщениями после подключения.</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await bot.send_message(bc.user_chat_id, "❌ Бот отключён от вашего аккаунта.")


@router.business_message()
async def on_business_message(message: Message, bot: Bot):
    conn_id = message.business_connection_id
    if not conn_id:
        return

    mtype, file_id = _extract_file_id(message)
    text = message.text or message.caption or ""
    date = int(message.date.timestamp()) if message.date else 0
    sender_id = message.from_user.id if message.from_user else 0

    # Download media bytes immediately so we can resend even after deletion
    media_data: Optional[bytes] = None
    if file_id:
        media_data = await _download_media(bot, file_id)

    await save_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_id=sender_id,
        sender_name=_sender_name(message),
        text=text,
        date=date,
        media_type=mtype,
        file_id=file_id,
        media_data=media_data,
    )


@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot):
    conn_id = message.business_connection_id
    if not conn_id:
        return

    conn = await get_connection(conn_id)
    if not conn:
        return

    # Skip own edits
    sender_id = message.from_user.id if message.from_user else 0
    if sender_id == conn["user_id"]:
        return

    cached = await get_message(conn_id, message.chat.id, message.message_id)
    old_text = cached["text"] if cached else "<i>(не сохранено)</i>"
    new_text = message.text or message.caption or ""
    sender = _sender_name(message)
    chat_label = message.chat.title or sender

    await bot.send_message(
        conn["user_chat_id"],
        f"✏️ <b>Сообщение изменено</b>\n\n"
        f"👤 {sender}  |  💬 {chat_label}\n\n"
        f"<b>Было:</b>\n{old_text}\n\n"
        f"<b>Стало:</b>\n{new_text}",
        parse_mode=ParseMode.HTML,
    )

    # Refresh cache
    mtype, file_id = _extract_file_id(message)
    media_data: Optional[bytes] = None
    if file_id:
        media_data = await _download_media(bot, file_id)

    await save_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_id=sender_id,
        sender_name=sender,
        text=new_text,
        date=int(message.date.timestamp()) if message.date else 0,
        media_type=mtype,
        file_id=file_id,
        media_data=media_data,
    )


@router.deleted_business_messages()
async def on_deleted_business_messages(event: BusinessMessagesDeleted, bot: Bot):
    conn_id = event.business_connection_id
    conn = await get_connection(conn_id)
    if not conn:
        return

    user_chat_id = conn["user_chat_id"]
    owner_id = conn["user_id"]
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

        # Don't notify when owner deletes their own messages
        if cached.get("sender_id") == owner_id:
            await delete_cached(conn_id, cached["chat_id"], msg_id)
            continue

        sender = cached["sender_name"] or "Unknown"
        text = cached["text"] or ""
        mtype = cached["media_type"]

        parts = [f"🗑 <b>Сообщение удалено</b>\n\n👤 {sender}"]
        if text:
            parts.append(f"\n\n💬 {text}")
        if mtype:
            parts.append(f"\n📎 <i>{mtype}</i>")

        await bot.send_message(user_chat_id, "".join(parts), parse_mode=ParseMode.HTML)
        await _send_cached_media(bot, user_chat_id, cached)
        await delete_cached(conn_id, cached["chat_id"], msg_id)


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
        "message",
    ])


if __name__ == "__main__":
    asyncio.run(main())
