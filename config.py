import os
import json
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Admin contact information
ADMIN_USERNAME = "@RahilAnw4r"
ADMIN_LINK = "https://t.me/RahilAnw4r"
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

# Optional: Restrict bot access to specific Telegram user IDs
_allowed_raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_TELEGRAM_USER_IDS = [
    int(uid.strip()) for uid in _allowed_raw.split(",") if uid.strip().isdigit()
]

# Optional: Path to cookies.txt or direct cookies content from environment variable
_cookies_content = os.getenv("COOKIES_CONTENT", "").strip() or os.getenv("YOUTUBE_COOKIES", "").strip()
_cookies_env = os.getenv("COOKIES_FILE", "").strip()

if _cookies_content:
    c_file = BASE_DIR / "cookies.txt"
    try:
        with open(c_file, "w", encoding="utf-8") as f:
            f.write(_cookies_content)
        COOKIES_FILE = c_file
    except Exception:
        COOKIES_FILE = None
elif _cookies_env and Path(_cookies_env).exists():
    COOKIES_FILE = Path(_cookies_env).resolve()
elif (BASE_DIR / "cookies.txt").exists():
    COOKIES_FILE = BASE_DIR / "cookies.txt"
else:
    COOKIES_FILE = None

# User download preferences (persistent to user_preferences.json)
_PREFS_FILE = BASE_DIR / "user_preferences.json"
_user_prefs: Dict[str, Any] = {}


def _load_prefs():
    global _user_prefs
    if _PREFS_FILE.exists():
        try:
            with open(_PREFS_FILE, "r", encoding="utf-8") as f:
                _user_prefs = json.load(f)
        except Exception:
            _user_prefs = {}


def _save_prefs():
    try:
        with open(_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(_user_prefs, f, indent=2)
    except Exception:
        pass


_load_prefs()


def is_user_allowed(user_id: int) -> bool:
    """Check if the user is authorized to use the bot."""
    if not ALLOWED_TELEGRAM_USER_IDS:
        return True  # Allow all users if whitelist is not set
    return user_id in ALLOWED_TELEGRAM_USER_IDS


def is_admin(user) -> bool:
    """Check if a Telegram user has admin privileges."""
    if not user:
        return False
    # Check username
    if getattr(user, "username", None) and user.username.lower().replace("@", "") == "rahilanw4r":
        return True
    # Check admin ID from env
    if ADMIN_ID and str(user.id) == ADMIN_ID:
        return True
    # If whitelist is configured, users in it have admin command access
    if ALLOWED_TELEGRAM_USER_IDS and user.id in ALLOWED_TELEGRAM_USER_IDS:
        return True
    return False


def get_user_mode(user_id: int) -> str:
    """
    Get user's download mode:
    'instant' -> Immediately downloads highest quality video (fastest, 0 clicks).
    'picker'  -> Shows interactive quality buttons (1080p, 720p, 480p, MP3).
    """
    return _user_prefs.get(str(user_id), {}).get("mode", "instant")


def set_user_mode(user_id: int, mode: str) -> None:
    """Set user's download mode ('instant' or 'picker')."""
    uid_str = str(user_id)
    if uid_str not in _user_prefs:
        _user_prefs[uid_str] = {}
    _user_prefs[uid_str]["mode"] = mode
    _save_prefs()

