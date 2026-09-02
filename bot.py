import asyncio
import io
import logging
from typing import Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    Message,
    Update,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import (
    BOT_TOKEN,
    WEBHOOK_URL,
    WEBHOOK_PATH,
    WEBHOOK_SECRET_TOKEN,
    PORT,
)
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
    """Download file by file_id. Returns None if too large, once-view blocked, or any error."""
    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_size and tg_file.file_size > MAX_MEDIA_BYTES:
            logger.info("skip download: %s bytes > 20 MB limit", tg_file.file_size)
            return None
        if not tg_file.file_path:
            # Telegram did not provide file_path — view-once or server restriction
            logger.info("no file_path for file_id=%s (view-once?)", file_id[:20])
            return None
        buf = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=buf)
        data = buf.getvalue()
        logger.info("downloaded %d bytes for file_id=%s", len(data), file_id[:20])
        return data
    except Exception as e:
        logger.warning("download_media failed (%s): %s", type(e).__name__, e)
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


_MEDIA_LABELS = {
    "photo": "фото", "video": "видео", "video_note": "кружок",
    "voice": "голосовое", "audio": "аудио", "document": "файл",
}


@router.business_message()
async def on_business_message(message: Message, bot: Bot):
    conn_id = message.business_connection_id
    if not conn_id:
        return

    conn = await get_connection(conn_id)
    if not conn:
        return

    sender_id = message.from_user.id if message.from_user else 0
    is_outgoing = sender_id == conn["user_id"]

    # ── Owner replied to a message → try to download the replied-to media ──────
    if is_outgoing and message.reply_to_message:
        replied = message.reply_to_message
        r_mtype, r_file_id = _extract_file_id(replied)

        if r_file_id:
            # Check if already cached with bytes
            existing = await get_message(conn_id, message.chat.id, replied.message_id)
            already_saved = existing and existing.get("media_data")

            if not already_saved:
                data = await _download_media(bot, r_file_id)
                if data:
                    label = _MEDIA_LABELS.get(r_mtype, r_mtype)
                    # Update / create cache entry with real bytes
                    r_sender_id = replied.from_user.id if replied.from_user else 0
                    await save_message(
                        connection_id=conn_id,
                        chat_id=message.chat.id,
                        message_id=replied.message_id,
                        sender_id=r_sender_id,
                        sender_name=_sender_name(replied),
                        text=replied.text or replied.caption or "",
                        date=int(replied.date.timestamp()) if replied.date else 0,
                        media_type=r_mtype,
                        file_id=r_file_id,
                        media_data=data,
                    )
                    await bot.send_message(
                        conn["user_chat_id"],
                        f"✅ <b>Одноразовый {label} сохранён!</b>",
                        parse_mode=ParseMode.HTML,
                    )
                    # Send the media immediately
                    await _send_cached_media(bot, conn["user_chat_id"], {
                        "media_type": r_mtype, "file_id": r_file_id, "media_data": data,
                    })
                else:
                    await bot.send_message(
                        conn["user_chat_id"],
                        "❌ Не удалось скачать медиа — возможно уже открыто или удалено.",
                        parse_mode=ParseMode.HTML,
                    )
        return  # Don't cache the outgoing reply itself

    if is_outgoing:
        return  # Ignore other outgoing messages

    # ── Incoming message from interlocutor: cache + try download ──────────────
    mtype, file_id = _extract_file_id(message)
    text = message.text or message.caption or ""
    date = int(message.date.timestamp()) if message.date else 0

    media_data: Optional[bytes] = None
    download_failed = False
    if file_id:
        media_data = await _download_media(bot, file_id)
        download_failed = media_data is None

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

    # Once-view: download blocked by Telegram — silently wait for owner's reply


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

        # Don't notify when owner deletes their own messages.
        # sender_id == 0 means cached before fix (unknown sender) — skip to be safe.
        s_id = cached.get("sender_id") or 0
        if s_id == owner_id or s_id == 0:
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


ALLOWED_UPDATES = [
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "message",
]


# ── Webhook / web server ─────────────────────────────────────────────────────

async def on_webhook_startup(bot: Bot):
    if not WEBHOOK_URL:
        logger.warning(
            "WEBHOOK_URL не задан — не могу зарегистрировать webhook в Telegram. "
            "Установи RAILWAY_PUBLIC_DOMAIN или WEBHOOK_URL."
        )
        return
    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET_TOKEN or None,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=False,
    )
    logger.info("Webhook установлен: %s", WEBHOOK_URL)


async def on_webhook_shutdown(bot: Bot):
    await bot.delete_webhook()
    logger.info("Webhook удалён")


def _make_webhook_handler(bot: Bot, dp: Dispatcher):
    async def handle_webhook(request: web.Request) -> web.Response:
        # Verify the request actually came from Telegram, if a secret is set.
        if WEBHOOK_SECRET_TOKEN:
            token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if token != WEBHOOK_SECRET_TOKEN:
                logger.warning("webhook: invalid secret token")
                return web.Response(status=401)

        try:
            data = await request.json()
        except Exception as e:
            logger.warning("webhook: failed to parse JSON body: %s", e)
            return web.Response(status=400)

        update = Update.model_validate(data, context={"bot": bot})

        # Acknowledge Telegram immediately; process update in the background
        # so slow handlers don't cause Telegram to retry delivery.
        asyncio.create_task(dp.feed_update(bot, update))

        return web.Response(status=200)

    return handle_webhook


async def health_check(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


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

    await on_webhook_startup(bot)

    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, _make_webhook_handler(bot, dp))
    app.router.add_get("/", health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info("Webhook сервер запущен на порту %d, путь %s", PORT, WEBHOOK_PATH)

    try:
        # Keep the process alive; updates are delivered via the webhook route.
        await asyncio.Event().wait()
    finally:
        await on_webhook_shutdown(bot)
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
