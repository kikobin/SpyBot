# Dialog Spy Bot

Telegram Business Bot — отслеживает удаление и редактирование сообщений в чатах.

## Возможности

- 🔔 **Удалённые сообщения** — присылает копию сообщения (текст + медиа) до того как оно исчезло
- ✏️ **Изменённые сообщения** — присылает старый и новый текст
- 📸 **Медиа** — фото, видео, голосовые, кружки — всё кэшируется автоматически

## Требования

- Telegram **Business** аккаунт (или Telegram Premium с функцией бизнес-чатов)
- Python 3.11+

## Запуск локально

```bash
git clone https://github.com/kikobin/SpyBot
cd SpyBot
pip install -r requirements.txt
cp .env.example .env
# Вставь токен бота в .env
python bot.py
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен от @BotFather (обязательно) |
| `DB_PATH` | Путь к SQLite базе (по умолчанию `messages.db`) |

Бот работает через **long polling** — это не требует публичного домена и проще
в настройке на Railway. Для надёжности при редеплоях используется
`drop_pending_updates=False`: любые обновления, накопившиеся за время простоя
бота, будут обработаны после перезапуска, а не потеряны. Также реализована
корректная обработка сигналов `SIGTERM`/`SIGINT` для плавной остановки.

## Деплой на Railway

1. Форкни репо или пуш свой
2. [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Добавь переменную `BOT_TOKEN` в Variables
4. Добавь **Volume** (Storage) с mount path `/data` и установи `DB_PATH=/data/messages.db` — иначе база сбросится при редеплое
5. Railway автоматически запустит `python bot.py` в режиме worker — публичный домен не нужен

## Подключение бота

1. Открой @BotFather → `/newbot` → получи токен
2. Telegram → Настройки → **Telegram Business** → Чат-боты
3. Введи `@username` своего бота
4. Напиши боту `/start` — он подтвердит подключение

## Стек

- [aiogram 3.x](https://docs.aiogram.dev/) — Telegram Bot Framework
- [aiosqlite](https://aiosqlite.omnilib.dev/) — асинхронный SQLite
- [Railway](https://railway.app) — хостинг
