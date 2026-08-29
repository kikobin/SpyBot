import os
import aiosqlite
from typing import Optional, List, Dict, Any

# On Railway: add a Volume mounted at /data, set DB_PATH=/data/messages.db
DB_PATH = os.getenv("DB_PATH", "messages.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS connections (
                connection_id TEXT PRIMARY KEY,
                user_chat_id  INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                is_enabled    INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS messages (
                connection_id TEXT    NOT NULL,
                chat_id       INTEGER NOT NULL,
                message_id    INTEGER NOT NULL,
                sender_name   TEXT,
                text          TEXT,
                date          INTEGER,
                media_type    TEXT,
                file_id       TEXT,
                PRIMARY KEY (connection_id, chat_id, message_id)
            );
        """)
        await db.commit()


# ── Connections ──────────────────────────────────────────────────────────────

async def save_connection(connection_id: str, user_chat_id: int, user_id: int, is_enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO connections (connection_id, user_chat_id, user_id, is_enabled)
               VALUES (?,?,?,?)""",
            (connection_id, user_chat_id, user_id, int(is_enabled)),
        )
        await db.commit()


async def get_user_chat_id(connection_id: str) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_chat_id FROM connections WHERE connection_id=? AND is_enabled=1",
            (connection_id,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


# ── Messages ─────────────────────────────────────────────────────────────────

async def save_message(connection_id: str, chat_id: int, message_id: int,
                       sender_name: str, text: str, date: int,
                       media_type: Optional[str] = None, file_id: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO messages
               (connection_id, chat_id, message_id, sender_name, text, date, media_type, file_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (connection_id, chat_id, message_id, sender_name, text, date, media_type, file_id),
        )
        await db.commit()


def _row(row) -> Dict[str, Any]:
    return {
        "connection_id": row[0], "chat_id": row[1], "message_id": row[2],
        "sender_name": row[3], "text": row[4], "date": row[5],
        "media_type": row[6], "file_id": row[7],
    }

_SEL = ("SELECT connection_id,chat_id,message_id,sender_name,text,date,media_type,file_id "
        "FROM messages ")


async def get_message(connection_id: str, chat_id: int, message_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            _SEL + "WHERE connection_id=? AND chat_id=? AND message_id=?",
            (connection_id, chat_id, message_id),
        ) as cur:
            row = await cur.fetchone()
    return _row(row) if row else None


async def find_by_message_id(connection_id: str, message_id: int) -> List[Dict[str, Any]]:
    """Fallback when chat_id unknown (deleted_business_messages might omit it)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            _SEL + "WHERE connection_id=? AND message_id=?",
            (connection_id, message_id),
        ) as cur:
            rows = await cur.fetchall()
    return [_row(r) for r in rows]


async def delete_cached(connection_id: str, chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?",
            (connection_id, chat_id, message_id),
        )
        await db.commit()
