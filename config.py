import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# ── Webhook configuration ────────────────────────────────────────────────────
# On Railway, RAILWAY_PUBLIC_DOMAIN is injected automatically for services
# with a public domain enabled. WEBHOOK_URL can be set explicitly to override.
_RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN")

WEBHOOK_PATH: str = os.getenv("WEBHOOK_PATH", "/webhook")

WEBHOOK_URL: str = os.getenv(
    "WEBHOOK_URL",
    f"https://{_RAILWAY_PUBLIC_DOMAIN}{WEBHOOK_PATH}" if _RAILWAY_PUBLIC_DOMAIN else "",
)

# Secret token Telegram will echo back in the X-Telegram-Bot-Api-Secret-Token
# header on every webhook request, so we can verify requests actually come
# from Telegram.
WEBHOOK_SECRET_TOKEN: str = os.getenv("WEBHOOK_SECRET_TOKEN", "")

# Port the aiohttp server listens on. Railway injects PORT automatically.
PORT: int = int(os.getenv("PORT", "8080"))
