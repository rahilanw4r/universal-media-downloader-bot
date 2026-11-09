import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"
LEGACY_STATS_FILE = BASE_DIR / "bot_stats.json"

_lock = threading.RLock()
_process_start_time = time.time()


def _get_connection() -> sqlite3.Connection:
    """Create a thread-safe connection to the SQLite database with WAL mode enabled."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _init_db() -> None:
    """Initialize database tables and import legacy bot_stats.json if needed."""
    with _lock:
        with _get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    interactions INTEGER DEFAULT 1,
                    downloads INTEGER DEFAULT 0,
                    is_blocked INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS platforms (
                    platform TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media_types (
                    media_type TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('started_at', ?)", (now_iso,))
            conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('total_downloads_offset', '0')")
            conn.commit()

        _migrate_legacy_json()


def _migrate_legacy_json() -> None:
    """Migrate historical data from bot_stats.json if SQLite database is empty."""
    if not LEGACY_STATS_FILE.exists():
        return

    try:
        with open(LEGACY_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return

        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            user_count = cur.fetchone()[0]

            if user_count == 0 and "users" in data:
                for uid_str, u in data.get("users", {}).items():
                    try:
                        uid = int(uid_str)
                    except ValueError:
                        continue
                    cur.execute("""
                        INSERT OR REPLACE INTO users
                        (user_id, username, first_name, first_seen, last_seen, interactions, downloads, is_blocked)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """, (
                        uid,
                        u.get("username", ""),
                        u.get("first_name", ""),
                        u.get("first_seen", datetime.now(timezone.utc).isoformat()),
                        u.get("last_seen", datetime.now(timezone.utc).isoformat()),
                        u.get("interactions", 1),
                        u.get("downloads", 0),
                    ))

                if "platforms" in data:
                    for plat, cnt in data["platforms"].items():
                        cur.execute("INSERT OR REPLACE INTO platforms (platform, count) VALUES (?, ?)", (plat, cnt))

                if "media_types" in data:
                    for mt, cnt in data["media_types"].items():
                        cur.execute("INSERT OR REPLACE INTO media_types (media_type, count) VALUES (?, ?)", (mt, cnt))

                if "started_at" in data:
                    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('started_at', ?)", (data["started_at"],))

                legacy_total = data.get("total_downloads", 0)
                if legacy_total:
                    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('total_downloads_offset', ?)", (str(legacy_total),))

                conn.commit()
    except Exception:
        pass


_init_db()


def track_user(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
    """Record or update user activity (first seen, last seen, interaction count) permanently."""
    if not user_id:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    with _lock:
        try:
            with _get_connection() as conn:
                conn.execute("""
                    INSERT INTO users (user_id, username, first_name, first_seen, last_seen, interactions, downloads, is_blocked)
                    VALUES (?, ?, ?, ?, ?, 1, 0, 0)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = CASE WHEN excluded.username != '' THEN excluded.username ELSE users.username END,
                        first_name = CASE WHEN excluded.first_name != '' THEN excluded.first_name ELSE users.first_name END,
                        last_seen = excluded.last_seen,
                        interactions = users.interactions + 1,
                        is_blocked = 0
                """, (user_id, username or "", first_name or "", now_iso, now_iso))
                conn.commit()
        except Exception:
            pass


def track_download(user_id: int, platform: str, media_type: str = "video") -> None:
    """Record a completed media download permanently in SQLite."""
    now_iso = datetime.now(timezone.utc).isoformat()
    plat_clean = platform.strip() if platform else "Other"
    mtype_clean = media_type.strip().lower() if media_type else "video"

    with _lock:
        try:
            with _get_connection() as conn:
                conn.execute("""
                    UPDATE users SET downloads = downloads + 1, last_seen = ? WHERE user_id = ?
                """, (now_iso, user_id))

                conn.execute("""
                    INSERT INTO platforms (platform, count) VALUES (?, 1)
                    ON CONFLICT(platform) DO UPDATE SET count = platforms.count + 1
                """, (plat_clean,))

                conn.execute("""
                    INSERT INTO media_types (media_type, count) VALUES (?, 1)
                    ON CONFLICT(media_type) DO UPDATE SET count = media_types.count + 1
                """, (mtype_clean,))

                conn.commit()
        except Exception:
            pass


def get_all_user_ids(include_blocked: bool = False) -> List[int]:
    """Get list of all recorded unique Telegram user IDs."""
    with _lock:
        try:
            with _get_connection() as conn:
                cur = conn.cursor()
                if include_blocked:
                    cur.execute("SELECT user_id FROM users ORDER BY last_seen DESC")
                else:
                    cur.execute("SELECT user_id FROM users WHERE is_blocked = 0 ORDER BY last_seen DESC")
                rows = cur.fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []


def mark_user_blocked(user_id: int) -> None:
    """Mark a user as having blocked the bot to avoid redundant broadcast attempts."""
    with _lock:
        try:
            with _get_connection() as conn:
                conn.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))
                conn.commit()
        except Exception:
            pass


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


def format_uptime() -> str:
    """Format bot process uptime into friendly human-readable duration (e.g. 1d 4h 12m 30s)."""
    uptime_sec = max(0, int(time.time() - _process_start_time))
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def get_analytics_summary() -> Dict[str, Any]:
    """Compute rich statistics from the permanent SQLite database."""
    with _lock:
        try:
            with _get_connection() as conn:
                cur = conn.cursor()

                cur.execute("SELECT COUNT(*) FROM users")
                total_users = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 0")
                active_users = cur.fetchone()[0]

                cur.execute("SELECT SUM(downloads) FROM users")
                users_downloads = cur.fetchone()[0] or 0

                cur.execute("SELECT SUM(count) FROM platforms")
                platforms_downloads = cur.fetchone()[0] or 0
                total_downloads = max(users_downloads, platforms_downloads)

                cur.execute("SELECT SUM(interactions) FROM users")
                total_interactions = cur.fetchone()[0] or 0

                now = datetime.now(timezone.utc)
                active_today = 0
                active_week = 0

                cur.execute("SELECT last_seen FROM users")
                for (last_seen_str,) in cur.fetchall():
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

                cur.execute("SELECT platform, count FROM platforms ORDER BY count DESC")
                platforms = [(r[0], r[1]) for r in cur.fetchall()]

                cur.execute("SELECT media_type, count FROM media_types")
                media_types = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT user_id, username, first_name, last_seen, downloads
                    FROM users ORDER BY last_seen DESC LIMIT 5
                """)
                recent_users = []
                for r in cur.fetchall():
                    recent_users.append({
                        "id": r[0],
                        "username": r[1],
                        "first_name": r[2],
                        "last_seen": r[3],
                        "downloads": r[4],
                    })

                cur.execute("SELECT value FROM meta WHERE key = 'started_at'")
                row = cur.fetchone()
                started_at = row[0] if row else now.isoformat()

                return {
                    "total_users": total_users,
                    "active_users": active_users,
                    "active_today": active_today,
                    "active_week": active_week,
                    "total_downloads": total_downloads,
                    "total_interactions": total_interactions,
                    "platforms": platforms,
                    "media_types": media_types,
                    "recent_users": recent_users,
                    "started_at": started_at,
                }
        except Exception:
            return {
                "total_users": 0,
                "active_users": 0,
                "active_today": 0,
                "active_week": 0,
                "total_downloads": 0,
                "total_interactions": 0,
                "platforms": [],
                "media_types": {},
                "recent_users": [],
                "started_at": datetime.now(timezone.utc).isoformat(),
            }


def format_admin_dashboard() -> str:
    """Generate HTML formatted dashboard message for admin."""
    summary = get_analytics_summary()

    total_users = summary["total_users"]
    active_today = summary["active_today"]
    active_week = summary["active_week"]
    total_downloads = summary["total_downloads"]
    total_interactions = summary["total_interactions"]

    plat_lines = []
    if summary["platforms"]:
        for plat, count in summary["platforms"][:7]:
            pct = (count / total_downloads * 100) if total_downloads > 0 else 0
            plat_lines.append(f"• <b>{plat}:</b> {count:,} (<i>{pct:.1f}%</i>)")
    plat_text = "\n".join(plat_lines) if plat_lines else "<i>No downloads recorded yet.</i>"

    mt = summary["media_types"]
    vid_c = mt.get("video", 0)
    aud_c = mt.get("audio", 0)
    pho_c = mt.get("photo", 0) + mt.get("album", 0)
    doc_c = mt.get("document", 0)
    art_c = mt.get("article", 0)

    format_lines = [
        f"• 🎬 <b>Videos:</b> {vid_c:,}",
        f"• 🎵 <b>MP3 Audio:</b> {aud_c:,}",
        f"• 📸 <b>Photos / Carousels:</b> {pho_c:,}",
    ]
    if doc_c > 0:
        format_lines.append(f"• 📦 <b>Documents / Files:</b> {doc_c:,}")
    if art_c > 0:
        format_lines.append(f"• 📰 <b>Articles:</b> {art_c:,}")
    formats_text = "\n".join(format_lines)

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
        f"{formats_text}\n\n"
        "🕒 <b>Recent Users:</b>\n"
        f"{recent_text}\n\n"
        "💡 <i>Tip: Send <code>/broadcast &lt;message&gt;</code> to send an announcement to all users.</i>"
    )


def format_public_dashboard() -> str:
    """Generate public community stats for regular users."""
    summary = get_analytics_summary()
    total_users = summary["total_users"]
    total_downloads = summary["total_downloads"]

    return (
        "📊 <b>Universal Media Downloader Statistics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Registered users:</b> <code>{total_users:,}</code>\n"
        f"📥 <b>Recorded downloads:</b> <code>{total_downloads:,}</code>\n"
        "⚡ <b>Supported:</b> Instagram, YouTube, TikTok, Pinterest, X, Reddit & 1,000+ sites\n\n"
        "💬 <i>Enjoying the bot? Share it with friends or add it to your group chats!</i>"
    )
