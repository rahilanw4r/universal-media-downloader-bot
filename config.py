import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Fallback minimal .env loader if python-dotenv is not installed
    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        try:
            with open(_env_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _v = _line.split("=", 1)
                        os.environ.setdefault(_k.strip(), _v.strip())
        except Exception:
            pass

BASE_DIR = Path(__file__).resolve().parent

# Application Version
BOT_VERSION = "2.6.0"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Optional DiskWala extraction API key and custom resolver URL
DISKWALA_API_KEY = os.getenv("DISKWALA_API_KEY", "").strip()
DISKWALA_API_URL = os.getenv("DISKWALA_API_URL", "").strip()

# Admin contact information
ADMIN_USERNAME = "@RahilAnw4r"
ADMIN_LINK = "https://t.me/RahilAnw4r"
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
ADMIN_USER_IDS = set()
if ADMIN_ID:
    for _aid in ADMIN_ID.split(","):
        _aid = _aid.strip()
        if _aid.isdigit():
            ADMIN_USER_IDS.add(int(_aid))

# Telegram Bot API Server Configuration
# Leave empty or unset to use official Telegram Cloud API (50 MB upload limit).
# Set to local server URL (e.g. "http://localhost:8081" or "http://127.0.0.1:8081") to unlock up to 2000 MB (2 GB) uploads!
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "").strip()
# Set to True if the bot and local Bot API server share filesystem access for instant zero-copy delivery
LOCAL_BOT_API_LOCAL_MODE = os.getenv("LOCAL_BOT_API_LOCAL_MODE", "false").lower() in ("1", "true", "yes")

# Maximum upload file size in megabytes (Default: 50 MB on official cloud API; 2000 MB if Local Bot API is set)
_default_max_size = 2000.0 if LOCAL_BOT_API_URL else 50.0
try:
    MAX_FILE_SIZE_MB = float(os.getenv("MAX_FILE_SIZE_MB", str(_default_max_size)))
except ValueError:
    MAX_FILE_SIZE_MB = _default_max_size

# Automatically split oversized videos into seamless parts with zero quality loss
AUTO_SPLIT_OVERSIZED_VIDEOS = os.getenv("AUTO_SPLIT_OVERSIZED_VIDEOS", "true").lower() in ("1", "true", "yes")

# Optional lossy compression (Default: False to preserve 100% original quality)
ENABLE_VIDEO_COMPRESSION = os.getenv("ENABLE_VIDEO_COMPRESSION", "false").lower() in ("1", "true", "yes")

# Forced channel membership (set to empty or "none" to disable)
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL", "@Bootscreen").strip()

# Optional: Restrict bot access to specific Telegram user IDs
_allowed_raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_TELEGRAM_USER_IDS = [
    int(uid.strip()) for uid in _allowed_raw.split(",") if uid.strip().isdigit()
]

# Optional: Path to cookies.txt or direct cookies content from environment variable
COOKIES_FILE: Optional[Path] = None


def _clean_cookie_content(raw: str) -> str:
    """Normalize raw cookie string, supporting base64 encoding, literal \\n, and Netscape headers."""
    import base64

    raw = raw.strip()
    if not raw:
        return ""

    # Check if base64 encoded
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8", errors="ignore")
        if "youtube.com" in decoded or "Netscape" in decoded or "\t" in decoded:
            raw = decoded
    except Exception:
        pass

    # Normalize literal \r\n and \n sequences from single-line environment variables
    if "\\n" in raw:
        raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n")

    # Ensure valid Netscape HTTP Cookie file header
    if not raw.startswith("# Netscape") and not raw.startswith("# HTTP"):
        raw = "# Netscape HTTP Cookie File\n" + raw

    return raw.strip() + "\n"


def reload_cookies() -> Optional[Path]:
    """Reload and validate cookies file from environment variable or disk."""
    global COOKIES_FILE

    _cookies_content = os.getenv("COOKIES_CONTENT", "").strip() or os.getenv("YOUTUBE_COOKIES", "").strip()
    _cookies_env = os.getenv("COOKIES_FILE", "").strip()

    if _cookies_content:
        c_file = BASE_DIR / "cookies.txt"
        try:
            cleaned = _clean_cookie_content(_cookies_content)
            with open(c_file, "w", encoding="utf-8") as f:
                f.write(cleaned)
            COOKIES_FILE = c_file
            return COOKIES_FILE
        except Exception:
            pass

    if _cookies_env and Path(_cookies_env).exists():
        COOKIES_FILE = Path(_cookies_env).resolve()
        return COOKIES_FILE

    default_file = BASE_DIR / "cookies.txt"
    if default_file.exists() and default_file.stat().st_size > 10:
        COOKIES_FILE = default_file
        return COOKIES_FILE

    COOKIES_FILE = None
    return None


def save_cookies_file(content: str) -> bool:
    """Save updated cookies content into BASE_DIR / cookies.txt."""
    global COOKIES_FILE
    try:
        cleaned = _clean_cookie_content(content)
        if not cleaned:
            return False
        c_file = BASE_DIR / "cookies.txt"
        with open(c_file, "w", encoding="utf-8") as f:
            f.write(cleaned)
        COOKIES_FILE = c_file
        return True
    except Exception:
        return False


def get_cookies_status() -> Dict[str, Any]:
    """Get diagnostic information about currently loaded cookies."""
    reload_cookies()
    if not COOKIES_FILE or not COOKIES_FILE.exists():
        return {
            "loaded": False,
            "message": "No cookies file found. Bot is running in cookie-less mobile client bypass mode.",
            "path": None,
            "total_cookies": 0,
            "youtube_cookies": 0,
            "instagram_cookies": 0,
            "terabox_cookies": 0,
            "has_login": False,
            "has_ig_login": False,
            "has_tb_login": False,
        }

    try:
        size = COOKIES_FILE.stat().st_size
        lines = 0
        yt_count = 0
        ig_count = 0
        tb_count = 0
        has_login = False
        has_ig_login = False
        has_tb_login = bool(os.getenv("TERABOX_COOKIE") or os.getenv("NDUS"))
        with open(COOKIES_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                lines += 1
                if "youtube.com" in line:
                    yt_count += 1
                    if any(k in line for k in ("LOGIN_INFO", "SID", "SSID", "HSID", "__Secure-3PSID")):
                        has_login = True
                if "instagram.com" in line:
                    ig_count += 1
                    if any(k in line for k in ("sessionid", "ds_user_id")):
                        has_ig_login = True
                if "terabox" in line or "1024tera" in line or "ndus" in line:
                    tb_count += 1
                    if "ndus" in line:
                        has_tb_login = True

        yt_state = "logged in" if has_login else "guest"
        ig_state = "logged in" if has_ig_login else ("present" if ig_count > 0 else "none")
        tb_state = "logged in" if has_tb_login else ("present" if tb_count > 0 else "bypass")

        return {
            "loaded": True,
            "path": str(COOKIES_FILE),
            "size_bytes": size,
            "total_cookies": lines,
            "youtube_cookies": yt_count,
            "instagram_cookies": ig_count,
            "terabox_cookies": tb_count,
            "has_login": has_login,
            "has_ig_login": has_ig_login,
            "has_tb_login": has_tb_login,
            "message": f"Active ({yt_count} YouTube [{yt_state}], {ig_count} Instagram [{ig_state}], {tb_count} TeraBox [{tb_state}]).",
        }
    except Exception as e:
        return {
            "loaded": False,
            "message": f"Error reading cookies file: {e}",
            "path": str(COOKIES_FILE),
            "total_cookies": 0,
            "youtube_cookies": 0,
            "instagram_cookies": 0,
            "terabox_cookies": 0,
            "has_login": False,
            "has_ig_login": False,
            "has_tb_login": False,
        }


def get_api_server_info() -> Dict[str, Any]:
    """Get diagnostic information about the Telegram Bot API server and file limits."""
    is_local = bool(LOCAL_BOT_API_URL)
    return {
        "version": BOT_VERSION,
        "is_local": is_local,
        "base_url": LOCAL_BOT_API_URL if is_local else "https://api.telegram.org",
        "local_mode": LOCAL_BOT_API_LOCAL_MODE if is_local else False,
        "max_filesize_mb": MAX_FILE_SIZE_MB,
        "auto_split": AUTO_SPLIT_OVERSIZED_VIDEOS,
        "compression_enabled": ENABLE_VIDEO_COMPRESSION,
    }


# Initialize cookies on module load
reload_cookies()

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
    # Check developer username
    if getattr(user, "username", None) and user.username.lower().replace("@", "") == "rahilanw4r":
        return True
    # Check admin ID from env
    if getattr(user, "id", None) and (user.id in ADMIN_USER_IDS or (ADMIN_ID and str(user.id) == ADMIN_ID)):
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

