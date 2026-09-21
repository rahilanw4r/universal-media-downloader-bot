import json
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

BASE_DIR = Path(__file__).resolve().parent
STATS_FILE = BASE_DIR / "bot_stats.json"

_lock = threading.Lock()

# In-memory stats cache
_stats: Dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "total_downloads": 0,
    "total_interactions": 0,
    "users": {},
    "platforms": {},
    "media_types": {},
}


def _load_stats() -> None:
    """Load persistent stats from bot_stats.json."""
    global _stats
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Ensure base structure keys exist
                    for key in ["started_at", "total_downloads", "total_interactions", "users", "platforms", "media_types"]:
                        if key in data:
                            _stats[key] = data[key]
        except Exception:
            pass


def _save_stats() -> None:
    """Persist current stats to bot_stats.json."""
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(_stats, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# Initialize on import
_load_stats()


def track_user(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
    """Record or update user activity (first seen, last seen, interaction count)."""
    if not user_id:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    uid_str = str(user_id)

    with _lock:
        _stats["total_interactions"] = _stats.get("total_interactions", 0) + 1

        if "users" not in _stats:
            _stats["users"] = {}

        if uid_str not in _stats["users"]:
            _stats["users"][uid_str] = {
                "id": user_id,
                "username": username or "",
                "first_name": first_name or "",
                "first_seen": now_iso,
                "last_seen": now_iso,
                "interactions": 1,
                "downloads": 0,
            }
        else:
            user_entry = _stats["users"][uid_str]
            user_entry["last_seen"] = now_iso
            user_entry["interactions"] = user_entry.get("interactions", 0) + 1
            if username:
                user_entry["username"] = username
            if first_name:
                user_entry["first_name"] = first_name

        _save_stats()


def track_download(user_id: int, platform: str, media_type: str = "video") -> None:
    """Record a completed media download."""
    uid_str = str(user_id)
    with _lock:
        _stats["total_downloads"] = _stats.get("total_downloads", 0) + 1

        # Track per-platform count
        plat_clean = platform.strip() if platform else "Other"
        if "platforms" not in _stats:
            _stats["platforms"] = {}
        _stats["platforms"][plat_clean] = _stats["platforms"].get(plat_clean, 0) + 1

        # Track media type count (video, audio, photo, album)
        mtype_clean = media_type.strip().lower() if media_type else "video"
        if "media_types" not in _stats:
            _stats["media_types"] = {}
        _stats["media_types"][mtype_clean] = _stats["media_types"].get(mtype_clean, 0) + 1

        # Update per-user downloads
        if "users" in _stats and uid_str in _stats["users"]:
            _stats["users"][uid_str]["downloads"] = _stats["users"][uid_str].get("downloads", 0) + 1

        _save_stats()


def get_all_user_ids() -> List[int]:
    """Get list of all recorded unique Telegram user IDs."""
    with _lock:
        ids = []
        for uid_str in _stats.get("users", {}).keys():
            try:
                ids.append(int(uid_str))
            except ValueError:
                pass
        return ids


def _time_ago(iso_str: str) -> str:
    """Format ISO timestamp into friendly relative time string (e.g. 5m ago, 2h ago)."""
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        diff_sec = max(0, int((now - dt).total_seconds()))
        if diff_sec < 60:
            return f"{diff_sec}s ago"
        elif diff_sec < 3600:
            return f"{diff_sec // 60}m ago"
        elif diff_sec < 86400:
            return f"{diff_sec // 3600}h ago"
        else:
            return f"{diff_sec // 86400}d ago"
    except Exception:
        return "recently"


def get_analytics_summary() -> Dict[str, Any]:
    """Compute rich statistics for admin view."""
    with _lock:
        users = _stats.get("users", {})
        total_users = len(users)
        total_downloads = _stats.get("total_downloads", 0)
        total_interactions = _stats.get("total_interactions", 0)

        now = datetime.now(timezone.utc)
        active_today = 0
        active_week = 0

        # Sort users by last_seen descending
        user_list = list(users.values())
        for u in user_list:
            last_seen_str = u.get("last_seen")
            if last_seen_str:
                try:
                    dt = datetime.fromisoformat(last_seen_str)
                    age_seconds = (now - dt).total_seconds()
                    if age_seconds <= 86400:
                        active_today += 1
                    if age_seconds <= 7 * 86400:
                        active_week += 1
                except Exception:
                    pass

        # Sort platforms by count
        platforms = sorted(
            _stats.get("platforms", {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )

        # Media types
        media_types = _stats.get("media_types", {})

        # Recent active users (up to 5)
        def _sort_key(entry):
            return entry.get("last_seen", "")

        user_list.sort(key=_sort_key, reverse=True)
        recent_users = user_list[:5]

        return {
            "total_users": total_users,
            "active_today": active_today,
            "active_week": active_week,
            "total_downloads": total_downloads,
            "total_interactions": total_interactions,
            "platforms": platforms,
            "media_types": media_types,
            "recent_users": recent_users,
            "started_at": _stats.get("started_at"),
        }


def format_admin_dashboard() -> str:
    """Generate HTML formatted dashboard message for admin."""
    summary = get_analytics_summary()

    total_users = summary["total_users"]
    active_today = summary["active_today"]
    active_week = summary["active_week"]
    total_downloads = summary["total_downloads"]
    total_interactions = summary["total_interactions"]

    # Platform breakdown
    plat_lines = []
    if summary["platforms"]:
        for plat, count in summary["platforms"][:7]:
            pct = (count / total_downloads * 100) if total_downloads > 0 else 0
            plat_lines.append(f"• <b>{plat}:</b> {count:,} (<i>{pct:.1f}%</i>)")
    plat_text = "\n".join(plat_lines) if plat_lines else "<i>No downloads recorded yet.</i>"

    # Media breakdown
    mt = summary["media_types"]
    vid_c = mt.get("video", 0)
    aud_c = mt.get("audio", 0)
    pho_c = mt.get("photo", 0) + mt.get("album", 0)

    # Recent active users
    recent_lines = []
    for idx, u in enumerate(summary["recent_users"], 1):
        uname = f"@{u['username']}" if u.get("username") else (u.get("first_name") or f"ID:{u.get('id')}")
        dls = u.get("downloads", 0)
        time_ago = _time_ago(u.get("last_seen", ""))
        recent_lines.append(f"{idx}. <b>{uname}</b> — {dls} dls ({time_ago})")
    recent_text = "\n".join(recent_lines) if recent_lines else "<i>No users yet.</i>"

    return (
        "📊 <b>Bot Analytics & User Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Users:</b> <code>{total_users:,}</code>\n"
        f"⚡ <b>Active Today (24h):</b> <code>{active_today:,}</code>\n"
        f"📅 <b>Active This Week (7d):</b> <code>{active_week:,}</code>\n"
        f"📥 <b>Total Downloads:</b> <code>{total_downloads:,}</code>\n"
        f"💬 <b>Total Interactions:</b> <code>{total_interactions:,}</code>\n\n"
        "🌐 <b>Downloads by Platform:</b>\n"
        f"{plat_text}\n\n"
        "📦 <b>Media Formats:</b>\n"
        f"• 🎬 <b>Videos:</b> {vid_c:,}\n"
        f"• 🎵 <b>MP3 Audio:</b> {aud_c:,}\n"
        f"• 📸 <b>Photos / Carousels:</b> {pho_c:,}\n\n"
        "🕒 <b>Recent Users:</b>\n"
        f"{recent_text}\n\n"
        "💡 <i>Tip: Send <code>/broadcast &lt;message&gt;</code> to send an announcement to all users.</i>"
    )


def format_public_dashboard() -> str:
    """Generate public community stats for regular users."""
    summary = get_analytics_summary()
    total_users = max(summary["total_users"], 1)
    total_downloads = summary["total_downloads"]

    return (
        "📊 <b>Universal Media Downloader Statistics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Community:</b> <code>{total_users:,}+</code> happy users\n"
        f"📥 <b>Media Delivered:</b> <code>{total_downloads:,}+</code> files\n"
        "⚡ <b>Supported:</b> Instagram, YouTube, TikTok, Pinterest, X, Reddit & 1,000+ sites\n"
        "🚀 <b>Performance:</b> Ultra-fast 1080p downloads with zero compression loss!\n\n"
        "💬 <i>Enjoying the bot? Share it with friends or add it to your group chats!</i>"
    )
