import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Optional: Restrict bot access to specific Telegram user IDs
_allowed_raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_TELEGRAM_USER_IDS = [
    int(uid.strip()) for uid in _allowed_raw.split(",") if uid.strip().isdigit()
]

# Optional: Path to cookies.txt for platforms requiring login (Instagram, etc.)
_cookies_env = os.getenv("COOKIES_FILE", "").strip()
if _cookies_env and Path(_cookies_env).exists():
    COOKIES_FILE = Path(_cookies_env).resolve()
elif (BASE_DIR / "cookies.txt").exists():
    COOKIES_FILE = BASE_DIR / "cookies.txt"
else:
    COOKIES_FILE = None


def is_user_allowed(user_id: int) -> bool:
    """Check if the user is authorized to use the bot."""
    if not ALLOWED_TELEGRAM_USER_IDS:
        return True  # Allow all users if whitelist is not set
    return user_id in ALLOWED_TELEGRAM_USER_IDS
