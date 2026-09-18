import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PREFS_FILE = BASE_DIR / "user_preferences.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Optional: Restrict bot access to specific Telegram user IDs
_allowed_raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_TELEGRAM_USER_IDS = [
    int(uid.strip()) for uid in _allowed_raw.split(",") if uid.strip().isdigit()
]


def is_user_allowed(user_id: int) -> bool:
    """Check if the user is authorized to use the bot."""
    if not ALLOWED_TELEGRAM_USER_IDS:
        return True  # If not configured, allow everyone
    return user_id in ALLOWED_TELEGRAM_USER_IDS


def _load_prefs() -> dict:
    """Load user preferences from JSON file."""
    if PREFS_FILE.exists():
        try:
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_prefs(data: dict) -> None:
    """Save user preferences to JSON file."""
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving preferences: {e}")


def get_user_setting(user_id: int, key: str, default: any = None) -> any:
    """Retrieve a setting for a specific user."""
    prefs = _load_prefs()
    user_data = prefs.get(str(user_id), {})
    return user_data.get(key, default)


def set_user_setting(user_id: int, key: str, value: any) -> None:
    """Store a setting for a specific user."""
    prefs = _load_prefs()
    uid = str(user_id)
    if uid not in prefs:
        prefs[uid] = {}
    prefs[uid][key] = value
    _save_prefs(prefs)
