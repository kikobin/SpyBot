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
| `WEBHOOK_URL` | Полный URL вебхука (по умолчанию собирается из `RAILWAY_PUBLIC_DOMAIN` + `/webhook`) |
| `WEBHOOK_PATH` | Путь вебхука (по умолчанию `/webhook`) |
| `WEBHOOK_SECRET_TOKEN` | Секретный токен для проверки запросов от Telegram (рекомендуется) |
| `PORT` | Порт HTTP-сервера (по умолчанию `8080`, Railway задаёт автоматически) |

Бот работает через **webhook**, а не polling — это надёжнее на Railway, так как
не зависит от постоянного соединения, которое может рваться при редеплоях.

## Деплой на Railway

1. Форкни репо или пуш свой
2. [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Добавь переменную `BOT_TOKEN` в Variables
4. Включи **публичный домен** для сервиса (Settings → Networking → Generate Domain) — Railway автоматически задаст `RAILWAY_PUBLIC_DOMAIN`, из которого строится `WEBHOOK_URL`
5. (Рекомендуется) Добавь `WEBHOOK_SECRET_TOKEN` со случайным значением
6. Добавь **Volume** (Storage) с mount path `/data` и установи `DB_PATH=/data/messages.db` — иначе база сбросится при редеплое
7. Railway автоматически запустит `python bot.py`, при старте бот сам зарегистрирует webhook в Telegram

## Подключение бота

1. Открой @BotFather → `/newbot` → получи токен
2. Telegram → Настройки → **Telegram Business** → Чат-боты
3. Введи `@username` своего бота
4. Напиши боту `/start` — он подтвердит подключение

## Стек

- [aiogram 3.x](https://docs.aiogram.dev/) — Telegram Bot Framework
- [aiosqlite](https://aiosqlite.omnilib.dev/) — асинхронный SQLite
- [Railway](https://railway.app) — хостинг
