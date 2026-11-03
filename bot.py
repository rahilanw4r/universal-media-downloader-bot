import os
import sys
import html
import time
import uuid
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, Dict, Any, List
from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
import analytics
from downloader import (
    MediaDownloader,
    find_urls,
    detect_platform,
    render_progress_bar,
    DownloaderError,
    VIDEO_EXTS,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Initialize Downloader
downloader = MediaDownloader()

# Concurrency limiter to protect host from CPU/memory exhaustion
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
download_semaphore: Optional[asyncio.Semaphore] = None


def get_download_semaphore() -> asyncio.Semaphore:
    """Lazy initialize download semaphore on active event loop."""
    global download_semaphore
    if download_semaphore is None:
        download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    return download_semaphore

# Active URL sessions for quick actions
url_sessions: Dict[str, Dict[str, Any]] = {}


def cleanup_expired_sessions() -> None:
    """Purge sessions older than 1 hour to prevent memory buildup."""
    now = time.time()
    expired = [sid for sid, data in url_sessions.items() if now - data.get("time", 0) > 3600]
    for sid in expired:
        url_sessions.pop(sid, None)


# Active background download tasks per user / chat ID
user_active_tasks: Dict[int, List[asyncio.Task]] = {}


def register_user_task(user_id: int, task: asyncio.Task) -> None:
    """Register an active async task for a user so /stop can cancel it."""
    if user_id not in user_active_tasks:
        user_active_tasks[user_id] = []
    user_active_tasks[user_id].append(task)

    def _cleanup(_):
        tasks = user_active_tasks.get(user_id, [])
        if task in tasks:
            tasks.remove(task)
        if not tasks:
            user_active_tasks.pop(user_id, None)

    task.add_done_callback(_cleanup)


def cancel_user_tasks(user_id: int) -> int:
    """Cancel all active tasks for a user, returning count of tasks cancelled."""
    tasks = user_active_tasks.pop(user_id, [])
    cancelled = 0
    for task in tasks:
        if not task.done():
            task.cancel()
            cancelled += 1
    return cancelled


class ProgressTracker:
    """Handles rate-limited message editing for live download progress."""

    def __init__(self, message, loop: asyncio.AbstractEventLoop, platform: str):
        self.message = message
        self.loop = loop
        self.platform = platform
        self.last_update_time = 0.0
        self.last_percent = -1.0

    def on_progress(self, data: Dict[str, Any]):
        now = time.time()
        percent = data.get("percent", 0)

        # Update at most once every 1.6s to avoid Telegram rate limits
        if now - self.last_update_time < 1.6:
            return
        if abs(percent - self.last_percent) < 3.0 and percent < 98.0:
            return

        self.last_update_time = now
        self.last_percent = percent

        bar = render_progress_bar(percent, width=10)
        speed = data.get("speed", 0) or 0
        speed_mb = speed / (1024 * 1024)
        dl_mb = (data.get("downloaded", 0) or 0) / (1024 * 1024)
        total_mb = (data.get("total", 0) or 0) / (1024 * 1024)
        eta = data.get("eta", 0) or 0

        text = (
            f"⏳ <b>Downloading from {html.escape(self.platform)}...</b>\n\n"
            f"<code>{html.escape(bar)}</code>\n"
            f"⚡ <b>Speed:</b> {speed_mb:.1f} MB/s\n"
            f"📦 <b>Size:</b> {dl_mb:.1f} MB / {total_mb:.1f} MB\n"
            f"⏱️ <b>ETA:</b> {int(eta)}s"
        )

        async def _edit():
            try:
                await self.message.edit_text(text, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(_edit(), self.loop)


async def require_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Require users to join the public updates channel before using bot features."""
    user = update.effective_user
    if not user:
        return False

    # Admins always bypass channel requirement
    if config.is_admin(user):
        return True

    # Group and supergroup chats bypass channel requirement so group downloads work smoothly
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        return True

    channel = config.FORCE_SUB_CHANNEL
    if not channel or channel.lower() in ("none", "off", "false", "disable", "disabled"):
        return True

    clean_channel = channel.lstrip("@")
    channel_url = f"https://t.me/{clean_channel}"

    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user.id)
        if member.status in ("member", "administrator", "creator"):
            return True
    except Exception as e:
        # If bot is not in the channel or lacks permissions, fail open gracefully
        logger.warning(f"Could not verify membership in {channel}: {e}. Failing open.")
        return True

    message = (
        f"🔒 <b>Join our updates channel to use this bot!</b>\n\n"
        f"1. Tap <b>Join Channel</b> below to join {html.escape(channel)}.\n"
        f"2. Return here and tap <b>I've Joined</b> to continue."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=channel_url)],
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_membership")],
    ])
    if update.message:
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(message, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return False


async def check_user_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Authorize user, verify channel membership, and record user activity immediately."""
    user = update.effective_user
    if not user:
        return False

    # 1. Permanently track user in database immediately (every interaction is recorded forever)
    analytics.track_user(user.id, user.username, user.first_name)

    if not config.is_user_allowed(user.id):
        if update.effective_message:
            await update.effective_message.reply_text(
                f"⛔ <b>Access Denied</b>: You are not authorized to use this bot.\n"
                f"Your User ID is: <code>{user.id}</code>\n"
                f"Contact Admin: <a href=\"{config.ADMIN_LINK}\">{html.escape(config.ADMIN_USERNAME)}</a>",
                parse_mode=ParseMode.HTML,
            )
        return False

    return await require_channel_membership(update, context)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with rich greeting, command directory, and quick buttons."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    bot_username = context.bot.username if (context.bot and context.bot.username) else ""
    add_group_url = f"https://t.me/{bot_username}?startgroup=true" if bot_username else None

    welcome_text = (
        f"👋 <b>Hello, {html.escape(user.first_name)}!</b>\n\n"
        f"Welcome to <b>Universal Media Downloader</b> 📥\n"
        f"Download videos, photos, carousels & audio from <b>Instagram, YouTube, TikTok, X (Twitter), TeraBox, DiskWala, Pinterest, Reddit</b> & 1,000+ sites.\n\n"
        f"👥 <b>Works in Group Chats!</b>\n"
        f"Add me to any group chat and I'll automatically download and send every media link shared by members!\n\n"
        f"📌 <b>Quick Commands:</b>\n"
        f"• <b>Paste any link</b> — Auto-download media\n"
        f"• <code>/mp3 &lt;link&gt;</code> — Extract 192kbps audio with cover art\n"
        f"• <code>/stop</code> — Stop active downloads & clear sessions\n"
        f"• <code>/ping</code> — Check server latency & operational status\n"
        f"• <code>/stats</code> — View user analytics & download count\n"
        f"• <code>/help</code> — Full guide & instructions\n"
        f"• <code>/admin</code> — Contact developer (@RahilAnw4r)\n\n"
        f"💬 <b>Community discussion:</b> <a href=\"https://t.me/BootScreenCommunity\">Join @BootScreenCommunity</a>\n\n"
        f"👑 <b>Developer:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>"
    )

    keyboard = []
    if add_group_url:
        keyboard.append([InlineKeyboardButton("➕ Add Me to Your Group", url=add_group_url)])

    keyboard.extend([
        [
            InlineKeyboardButton("📖 User Guide & Help", callback_data="open_help"),
            InlineKeyboardButton("💬 Contact Admin", callback_data="open_admin"),
        ],
    ])

    await update.effective_message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    is_adm = config.is_admin(user)

    admin_section = ""
    if is_adm:
        admin_section = (
            "\n👑 <b>Admin Commands:</b>\n"
            "• <code>/changelog</code> — Latest release notes & announcement text\n"
            "• <code>/cookies</code> — Cookie status & diagnostics\n"
            "• <code>/clean</code> — Purge temporary download cache\n"
            "• <code>/broadcast &lt;msg&gt;</code> — Send announcement to all users\n"
            "• <i>Send cookies.txt file directly to update cookies</i>\n"
        )

    help_text = (
        "📖 <b>Universal Downloader — User Manual</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 <b>How to Download:</b>\n"
        "1. Send or forward any public video, photo, or album link.\n"
        "2. The bot automatically downloads and delivers the media file directly to your chat.\n\n"
        "🎵 <b>Audio Extraction (<code>/mp3</code>):</b>\n"
        "• Send <code>/mp3 &lt;link&gt;</code> to extract 192k audio with official album art.\n"
        "• Or tap <b>Extract MP3</b> directly under any sent video!\n\n"
        "👥 <b>Group Chats:</b>\n"
        "Add this bot to any group chat. When any member shares a link, the bot delivers the media directly into the group.\n\n"
        "📌 <b>All Commands:</b>\n"
        "• <code>/start</code> — Greeting, status & quick buttons\n"
        "• <code>/mp3 &lt;url&gt;</code> — Extract MP3 audio\n"
        "• <code>/stop</code> — Stop active downloads & clear sessions\n"
        "• <code>/ping</code> — Check server latency & health\n"
        "• <code>/stats</code> — User count & download analytics\n"
        "• <code>/admin</code> — Contact developer directly\n"
        "• <code>/about</code> — Bot technical specifications\n"
        "• <code>/help</code> — Show this manual\n"
        f"{admin_section}\n"
        f"💬 <b>Community discussion:</b> <a href=\"https://t.me/BootScreenCommunity\">Join @BootScreenCommunity</a>\n\n"
        f"👑 <b>Developer:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>"
    )

    keyboard = [
        [
            InlineKeyboardButton("💬 Chat with Admin", url=config.ADMIN_LINK),
        ]
    ]

    await update.effective_message.reply_text(
        help_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin and /contact commands."""
    if not await check_user_auth(update, context):
        return

    admin_text = (
        "👑 <b>Admin & Developer Support</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• <b>Developer:</b> Rahil Anwar\n"
        f"• <b>Telegram:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>\n"
        f"• <b>GitHub:</b> <a href=\"https://github.com/rahilanw4r\">github.com/rahilanw4r</a>\n\n"
        "💬 <i>Have questions, suggestions, or found a broken link? Click below to chat directly!</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("💬 Send Message to @RahilAnw4r", url=config.ADMIN_LINK),
        ]
    ]

    await update.message.reply_text(
        admin_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /about command."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    about_text = (
        "🤖 <b>Universal Media Downloader</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• <b>Version:</b> {config.BOT_VERSION}\n"
        "• <b>Architecture:</b> Python 3.11+ • yt-dlp • FFmpeg • Gallery-DL\n"
        f"• <b>Runtime:</b> Process uptime {analytics.format_uptime()}\n"
        f"• <b>Developer:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>\n\n"
        "High-performance media extractor built for speed and reliability."
    )

    row = []
    if config.is_admin(user):
        row.append(InlineKeyboardButton("📜 Changelog", callback_data="show_changelog"))
    row.append(InlineKeyboardButton("💬 Contact Admin", url=config.ADMIN_LINK))
    keyboard = [row]

    await update.message.reply_text(
        about_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def get_changelog_text() -> str:
    """Generate the latest release announcement text formatted for admin review and group broadcast."""
    return (
        f"📢 <b>Universal Media Downloader v{config.BOT_VERSION} Release Notes</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚀 <b>v2.6.0 (Latest Release):</b>\n\n"
        "• 🎵 <b>TikTok Direct Engine (No Watermark):</b>\n"
        "  Direct high-speed engine resolving ISP & regional network blocks. Delivers HD videos without watermarks, full photo carousel albums, and pristine 192kbps MP3 audio.\n\n"
        "• 📦 <b>DiskWala & TeraBox Overhaul:</b>\n"
        "  Full integration for DiskWala (diskwala.com, playdiskwala.in, diskwala.fun across /app, /s, /c) with direct stream resolvers. Overhauled TeraBox engine with fast redirect resolution, jsToken extraction, and NDUS session cookies.\n\n"
        "• 🛑 <b>Task Cancellation:</b>\n"
        "  Added <code>/stop</code> and <code>/cancel</code> commands to abort running downloads and clean up active sessions.\n\n"
        "• ⚡ <b>2 GB Uploads & Stream Splitting:</b>\n"
        "  Seamless Local Bot API integration with automated FFmpeg stream-copy splitting for large media files exceeding limits.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Tip: Forward this announcement directly to your community group or broadcast it to users with /broadcast!</i>"
    )


async def changelog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /changelog command - Displays latest release announcement (Admin only)."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    if not config.is_admin(user):
        await update.message.reply_text(
            "⛔ <b>Access Denied:</b> This command is restricted to the bot administrator.",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(get_changelog_text(), parse_mode=ParseMode.HTML)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats and /analytics commands."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    if config.is_admin(user):
        text = analytics.format_admin_dashboard()
        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh Stats", callback_data="refresh_stats"),
            ]
        ]
    else:
        text = analytics.format_public_dashboard()
        keyboard = [
            [
                InlineKeyboardButton("💬 Contact Admin", url=config.ADMIN_LINK),
            ]
        ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /broadcast <message> - Admin only announcement to all bot users."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    if not config.is_admin(user):
        await update.message.reply_text(
            "⛔ <b>Access Denied:</b> This command is restricted to the bot admin.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not context.args:
        await update.message.reply_text(
            "📢 <b>Broadcast Command Usage:</b>\n\n"
            "Send <code>/broadcast &lt;your message&gt;</code> to send an announcement to all registered users.",
            parse_mode=ParseMode.HTML,
        )
        return

    broadcast_text = " ".join(context.args)
    user_ids = analytics.get_all_user_ids(include_blocked=False)

    if not user_ids:
        await update.message.reply_text("⚠️ <i>No registered users found to broadcast to.</i>", parse_mode=ParseMode.HTML)
        return

    status_msg = await update.message.reply_text(
        f"⏳ <b>Broadcasting to {len(user_ids)} users...</b>",
        parse_mode=ParseMode.HTML,
    )

    sent = 0
    blocked = 0
    failed = 0
    formatted_msg = (
        f"📢 <b>Announcement from Admin</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{html.escape(broadcast_text)}\n\n"
        f"💬 <i>Universal Media Downloader Updates</i>"
    )

    for idx, uid in enumerate(user_ids, 1):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=formatted_msg,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
            await asyncio.sleep(0.04)  # ~25 msgs/sec safely under Telegram limit
        except Exception as e:
            err_str = str(e).lower()
            if "retry after" in err_str or "flood" in err_str:
                wait_sec = getattr(e, "retry_after", 5) or 5
                await asyncio.sleep(float(wait_sec) + 0.5)
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=formatted_msg,
                        parse_mode=ParseMode.HTML,
                    )
                    sent += 1
                except Exception:
                    failed += 1
            elif "blocked" in err_str or "forbidden" in err_str or "deactivated" in err_str:
                blocked += 1
                analytics.mark_user_blocked(uid)
            else:
                failed += 1

        # Periodic status update every 25 targets
        if idx % 25 == 0 or idx == len(user_ids):
            try:
                await status_msg.edit_text(
                    f"⏳ <b>Broadcasting in progress...</b>\n\n"
                    f"• Progress: {idx}/{len(user_ids)}\n"
                    f"• Delivered: {sent}\n"
                    f"• Blocked / Inactive: {blocked}\n"
                    f"• Errors: {failed}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"• 👥 <b>Total Targets:</b> {len(user_ids)}\n"
        f"• 📬 <b>Delivered:</b> {sent}\n"
        f"• 🚫 <b>Blocked / Inactive:</b> {blocked}\n"
        f"• ⚠️ <b>Errors / Failed:</b> {failed}",
        parse_mode=ParseMode.HTML,
    )


def get_promo_caption_footer() -> str:
    """Return promotional channel footer text for media captions."""
    if config.PROMO_CHANNELS_ENABLED and config.MOD_APK_CHANNEL and config.WALLPAPER_CHANNEL:
        return f"\n\n📢 <b>Join:</b> @{config.MOD_APK_CHANNEL} • @{config.WALLPAPER_CHANNEL}"
    return ""


def get_media_post_keyboard(vid_session_id: Optional[str] = None) -> Optional[InlineKeyboardMarkup]:
    """Build the action and channel promotion keyboard attached to media outputs."""
    rows = []
    if vid_session_id:
        rows.append([
            InlineKeyboardButton("🎵 Extract MP3 Audio", callback_data=f"dl:aud:{vid_session_id}"),
        ])
    if config.PROMO_CHANNELS_ENABLED and config.MOD_APK_CHANNEL and config.WALLPAPER_CHANNEL:
        rows.append([
            InlineKeyboardButton("📱 Mod APKs", url=config.MOD_APK_URL),
            InlineKeyboardButton("🖼️ Wallpapers", url=config.WALLPAPER_URL),
        ])
    return InlineKeyboardMarkup(rows) if rows else None


async def execute_download(
    chat_id: int,
    status_msg,
    target_url: str,
    platform: str,
    action: str,
    context: ContextTypes.DEFAULT_TYPE,
    resolution: Optional[int] = None,
) -> None:
    """Perform download with live progress bar and deliver media to Telegram with concurrency protection."""
    cur_task = asyncio.current_task()
    if cur_task:
        register_user_task(chat_id, cur_task)

    sem = get_download_semaphore()
    if sem.locked():
        try:
            await status_msg.edit_text(
                "⏳ <b>Queued...</b> Server is processing other downloads. Your task will start shortly!",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    async with sem:
        loop = asyncio.get_running_loop()
        tracker = ProgressTracker(status_msg, loop, platform)

        if action == "media":
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

            try:
                result = await downloader.download_media(
                    url=target_url,
                    resolution=resolution,
                    progress_callback=tracker.on_progress,
                )
                media_type = result.get("type", "video")
                dir_path = result.get("dir_path")
                title = result.get("title", "Media")
                duration = result.get("duration", 0)
                filesize_mb = result.get("filesize_mb", 0)
                bot_handle = f"@{context.bot.username}" if (context.bot and context.bot.username) else ""

                # Save session for quick post-download action buttons
                vid_session_id = uuid.uuid4().hex[:8]
                url_sessions[vid_session_id] = {
                    "url": target_url,
                    "platform": platform,
                    "time": time.time(),
                    "user_id": chat_id,
                }
                cleanup_expired_sessions()

                # 1. Single Photo delivery
                if media_type == "photo":
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                    file_path = result["file_path"]
                    caption = (
                        f"🖼️ <b>{html.escape(title[:100])}</b>\n"
                        f"📁 {html.escape(platform)} • 📦 {filesize_mb:.1f} MB"
                    )
                    if bot_handle:
                        caption += f"\n🤖 {html.escape(bot_handle)}"
                    caption += get_promo_caption_footer()
                    photo_keyboard = get_media_post_keyboard()

                    with open(file_path, "rb") as f:
                        try:
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=f,
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_markup=photo_keyboard,
                                read_timeout=300,
                                write_timeout=300,
                            )
                        except Exception as e:
                            err_str = str(e).lower()
                            if "parse entities" in err_str:
                                f.seek(0)
                                await context.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=f,
                                    caption=f"🖼️ {title[:100]}\n📁 {platform} • 📦 {filesize_mb:.1f} MB",
                                    reply_markup=photo_keyboard,
                                    read_timeout=300,
                                    write_timeout=300,
                                )
                            elif "image_process_failed" in err_str:
                                logger.warning("send_photo rejected with image_process_failed; falling back to document")
                                f.seek(0)
                                await context.bot.send_document(
                                    chat_id=chat_id,
                                    document=f,
                                    caption=caption,
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=photo_keyboard,
                                )
                            else:
                                raise
                    analytics.track_download(chat_id, platform, "photo")

                # 2. Multi-item album / carousel delivery
                elif media_type == "album":
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                    file_paths: List[Path] = result.get("file_paths", [])
                    caption = (
                        f"📸 <b>{html.escape(title[:100])}</b>\n"
                        f"📁 {html.escape(platform)} • 📦 {len(file_paths)} items ({filesize_mb:.1f} MB)"
                    )
                    if bot_handle:
                        caption += f"\n🤖 {html.escape(bot_handle)}"
                    caption += get_promo_caption_footer()

                    media = []
                    open_files = []
                    try:
                        for idx, fp in enumerate(file_paths[:10]):  # Telegram maximum is 10 items
                            fh = open(fp, "rb")
                            open_files.append(fh)
                            item_caption = caption if idx == 0 else None
                            if fp.suffix.lower() in VIDEO_EXTS:
                                media.append(InputMediaVideo(media=fh, caption=item_caption, parse_mode=ParseMode.HTML))
                            else:
                                media.append(InputMediaPhoto(media=fh, caption=item_caption, parse_mode=ParseMode.HTML))

                        try:
                            await context.bot.send_media_group(
                                chat_id=chat_id,
                                media=media,
                                read_timeout=300,
                                write_timeout=300,
                            )
                        except Exception as e:
                            err_str = str(e).lower()
                            if "parse entities" in err_str:
                                for item in media:
                                    item.parse_mode = None
                                    if item.caption:
                                        item.caption = f"📸 {title[:100]}\n📁 {platform} • 📦 {len(file_paths)} items ({filesize_mb:.1f} MB)"
                                    if hasattr(item.media, "seek"):
                                        item.media.seek(0)
                                await context.bot.send_media_group(
                                    chat_id=chat_id,
                                    media=media,
                                    read_timeout=300,
                                    write_timeout=300,
                                )
                            elif "image_process_failed" in err_str or "failed to send message" in err_str:
                                logger.warning("send_media_group failed with %s; falling back to individual items...", e)
                                for idx, fp in enumerate(file_paths[:10]):
                                    item_caption = caption if idx == 0 else None
                                    with open(fp, "rb") as item_fh:
                                        try:
                                            if fp.suffix.lower() in VIDEO_EXTS:
                                                await context.bot.send_video(
                                                    chat_id=chat_id,
                                                    video=item_fh,
                                                    caption=item_caption,
                                                    parse_mode=ParseMode.HTML,
                                                )
                                            else:
                                                await context.bot.send_photo(
                                                    chat_id=chat_id,
                                                    photo=item_fh,
                                                    caption=item_caption,
                                                    parse_mode=ParseMode.HTML,
                                                )
                                        except Exception:
                                            item_fh.seek(0)
                                            await context.bot.send_document(
                                                chat_id=chat_id,
                                                document=item_fh,
                                                caption=item_caption,
                                            )
                            else:
                                raise
                        analytics.track_download(chat_id, platform, "album")
                    finally:
                        for fh in open_files:
                            fh.close()

                # 3. Single Video delivery
                else:
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                    file_path = result["file_path"]
                    thumb_file = result.get("thumb_file")
                    width = result.get("width")
                    height = result.get("height")
                    is_comp = result.get("is_compressed", False)
                    is_split = result.get("is_split", False)
                    file_paths = result.get("file_paths", [file_path])

                    dur_str = f" • ⏱️ {int(duration)}s" if duration else ""
                    res_str = f" ({resolution}p)" if resolution else ""
                    comp_str = f"\n⚡ <i>Auto-compressed to fit Telegram {config.MAX_FILE_SIZE_MB:.0f}MB limit</i>" if is_comp else ""

                    # Quick action button & channel recommendation keyboard
                    post_keyboard = get_media_post_keyboard(vid_session_id)

                    thumb_handle = None
                    if thumb_file and Path(thumb_file).exists():
                        try:
                            thumb_handle = open(thumb_file, "rb")
                        except Exception:
                            thumb_handle = None

                    try:
                        # Multi-part video delivery if auto-split was triggered
                        if is_split and len(file_paths) > 1:
                            total_parts = len(file_paths)
                            for part_idx, part_path in enumerate(file_paths):
                                part_size_mb = part_path.stat().st_size / (1024 * 1024)
                                part_caption = (
                                    f"🎬 <b>{html.escape(title[:85])}</b> (Part {part_idx + 1}/{total_parts})\n"
                                    f"📁 {html.escape(platform)}{res_str} • 📦 {part_size_mb:.1f} MB\n"
                                    f"✂️ <i>Auto-split video delivery</i>"
                                )
                                if bot_handle:
                                    part_caption += f"\n🤖 {html.escape(bot_handle)}"
                                is_last = (part_idx == total_parts - 1)
                                if is_last:
                                    part_caption += get_promo_caption_footer()
                                with open(part_path, "rb") as pf:
                                    try:
                                        await context.bot.send_video(
                                            chat_id=chat_id,
                                            video=pf,
                                            thumbnail=thumb_handle,
                                            caption=part_caption,
                                            parse_mode=ParseMode.HTML,
                                            supports_streaming=True,
                                            reply_markup=post_keyboard if is_last else None,
                                            read_timeout=600,
                                            write_timeout=600,
                                        )
                                    except Exception as e:
                                        if "parse entities" in str(e).lower():
                                            pf.seek(0)
                                            await context.bot.send_video(
                                                chat_id=chat_id,
                                                video=pf,
                                                thumbnail=thumb_handle,
                                                caption=f"🎬 {title[:85]} (Part {part_idx + 1}/{total_parts})\n📁 {platform} • 📦 {part_size_mb:.1f} MB",
                                                supports_streaming=True,
                                                reply_markup=post_keyboard if is_last else None,
                                                read_timeout=600,
                                                write_timeout=600,
                                            )
                                        else:
                                            raise
                                    if thumb_handle and hasattr(thumb_handle, "seek"):
                                        thumb_handle.seek(0)
                        else:
                            caption = (
                                f"🎬 <b>{html.escape(title[:100])}</b>\n"
                                f"📁 {html.escape(platform)}{res_str} • 📦 {filesize_mb:.1f} MB{dur_str}{comp_str}"
                            )
                            if bot_handle:
                                caption += f"\n🤖 {html.escape(bot_handle)}"
                            caption += get_promo_caption_footer()

                            with open(file_path, "rb") as f:
                                try:
                                    await context.bot.send_video(
                                        chat_id=chat_id,
                                        video=f,
                                        thumbnail=thumb_handle,
                                        duration=int(duration) if duration else None,
                                        width=width,
                                        height=height,
                                        caption=caption,
                                        parse_mode=ParseMode.HTML,
                                        supports_streaming=True,
                                        reply_markup=post_keyboard,
                                        read_timeout=600,
                                        write_timeout=600,
                                    )
                                except Exception as e:
                                    if "parse entities" in str(e).lower():
                                        f.seek(0)
                                        if thumb_handle and hasattr(thumb_handle, "seek"):
                                            thumb_handle.seek(0)
                                        await context.bot.send_video(
                                            chat_id=chat_id,
                                            video=f,
                                            thumbnail=thumb_handle,
                                            duration=int(duration) if duration else None,
                                            width=width,
                                            height=height,
                                            caption=f"🎬 {title[:100]}\n📁 {platform}{res_str} • 📦 {filesize_mb:.1f} MB{dur_str}",
                                            supports_streaming=True,
                                            reply_markup=post_keyboard,
                                            read_timeout=600,
                                            write_timeout=600,
                                        )
                                    else:
                                        raise
                    finally:
                        if thumb_handle:
                            thumb_handle.close()

                    analytics.track_download(chat_id, platform, "video")

                # Cleanup temp files
                if dir_path:
                    downloader.cleanup(dir_path)
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            except asyncio.CancelledError:
                logger.info("Download task for chat %s was cancelled via /stop.", chat_id)
                try:
                    await status_msg.edit_text("🛑 <b>Download Stopped:</b> Task was cancelled.", parse_mode=ParseMode.HTML)
                except Exception:
                    pass
                raise
            except DownloaderError as e:
                logger.error(f"Download error: {e}")
                raw_err = str(e)
                if any(x in raw_err.lower() for x in ("login required", "sign in", "bot detection", "confirm you're not a bot", "ndus", "appicrypt", "terabox links require", "diskwala links are protected")):
                    help_note = (
                        "\n\n💡 <b>Configuration / Auth Notice:</b> This platform requires a valid session or API key. "
                        f"Admin can upload an updated <code>cookies.txt</code> using <code>/cookies</code> or configure credentials in <code>.env</code>."
                    )
                elif any(x in raw_err.lower() for x in ("too large", "limit", "exceeds")):
                    help_note = (
                        f"\n\n📦 <b>Size Limit Notice:</b> Current upload limit is {config.MAX_FILE_SIZE_MB:.0f}MB. "
                        "To upload files up to 2,000MB (2 GB), configure a Local Bot API server in <code>.env</code>."
                    )
                else:
                    help_note = f"\n\n💬 <i>Need help? Contact developer <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>.</i>"

                error_text = f"❌ <b>Download Failed:</b>\n{html.escape(raw_err)}{help_note}"
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=error_text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.exception("Unexpected error sending media")
                raw_err = str(e)
                if "image_process_failed" in raw_err.lower():
                    friendly_err = "Telegram rejected image format rendering. Please try again."
                elif "file is too big" in raw_err.lower():
                    friendly_err = f"File exceeds current {config.MAX_FILE_SIZE_MB:.0f}MB upload limit. Run a Local Bot API server to unlock 2,000MB (2 GB)."
                else:
                    friendly_err = html.escape(raw_err)

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"❌ <b>Error uploading media:</b> {friendly_err}\n\n"
                        f"💬 <i>Contact developer <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a> for assistance.</i>"
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )

        elif action == "audio":
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)

            try:
                result = await downloader.download_audio(
                    url=target_url,
                    progress_callback=tracker.on_progress,
                )
                file_path = result["file_path"]
                thumb_file = result.get("thumb_file")
                dir_path = result["dir_path"]
                title = result.get("title", "Audio")
                uploader = result.get("uploader", "Unknown")
                duration = result.get("duration", 0)
                filesize_mb = result.get("filesize_mb", 0)
                bot_handle = f"@{context.bot.username}" if (context.bot and context.bot.username) else ""

                thumb_handle = None
                if thumb_file and Path(thumb_file).exists():
                    thumb_handle = open(thumb_file, "rb")

                caption = f"🎵 <b>{html.escape(title[:100])}</b> (192kbps • {filesize_mb:.1f} MB)"
                if bot_handle:
                    caption += f"\n🤖 {html.escape(bot_handle)}"
                caption += get_promo_caption_footer()
                audio_keyboard = get_media_post_keyboard()

                try:
                    with open(file_path, "rb") as f:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=f,
                            thumbnail=thumb_handle,
                            title=title,
                            performer=uploader,
                            duration=duration,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=audio_keyboard,
                            read_timeout=300,
                            write_timeout=300,
                        )
                except Exception as e:
                    if "parse entities" in str(e).lower():
                        if thumb_handle and hasattr(thumb_handle, "seek"):
                            thumb_handle.seek(0)
                        with open(file_path, "rb") as f:
                            await context.bot.send_audio(
                                chat_id=chat_id,
                                audio=f,
                                thumbnail=thumb_handle,
                                title=title,
                                performer=uploader,
                                duration=duration,
                                caption=f"🎵 {title[:100]} (192kbps • {filesize_mb:.1f} MB)",
                                reply_markup=audio_keyboard,
                                read_timeout=300,
                                write_timeout=300,
                            )
                    else:
                        raise
                finally:
                    if thumb_handle:
                        thumb_handle.close()

                analytics.track_download(chat_id, platform, "audio")

                # Cleanup temp files
                if dir_path:
                    downloader.cleanup(dir_path)
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            except asyncio.CancelledError:
                logger.info("Download task for chat %s was cancelled via /stop.", chat_id)
                try:
                    await status_msg.edit_text("🛑 <b>Download Stopped:</b> Task was cancelled.", parse_mode=ParseMode.HTML)
                except Exception:
                    pass
                raise
            except DownloaderError as e:
                logger.error(f"Audio download error: {e}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"❌ <b>Audio Extraction Failed:</b>\n{html.escape(str(e))}\n\n"
                        f"💬 <i>Contact developer <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>.</i>"
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.exception("Unexpected error sending audio")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"❌ <b>Error uploading audio:</b> {html.escape(str(e))}\n\n"
                        f"💬 <i>Contact developer <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>.</i>"
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Extract links from user messages and trigger media download."""
    if not await check_user_auth(update, context):
        return

    message = update.message
    if not message or not message.text:
        return

    urls = find_urls(message.text)
    if not urls:
        return

    target_url = urls[0]
    platform = detect_platform(target_url)

    status_msg = await message.reply_text(
        f"⚡ <b>Connecting to {html.escape(platform)}...</b>",
        parse_mode=ParseMode.HTML,
    )
    await execute_download(
        chat_id=message.chat.id,
        status_msg=status_msg,
        target_url=target_url,
        platform=platform,
        action="media",
        context=context,
    )


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries (MP3 extraction, admin contact, help, and stats)."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    user_id = update.effective_user.id

    # Handle membership verification before the normal auth gate.
    if data == "check_membership":
        if await require_channel_membership(update, context):
            await query.answer("✅ Membership verified! Welcome aboard.", show_alert=True)
            await start_command(update, context)
        else:
            channel_name = config.FORCE_SUB_CHANNEL or "@Bootscreen"
            await query.answer(f"Please join {channel_name} first, then tap again!", show_alert=True)
        return

    if not await check_user_auth(update, context):
        return

    await query.answer()

    # Show Changelog (Admin only)
    if data == "show_changelog":
        if not config.is_admin(user):
            await query.answer("⛔ Access denied: This information is reserved for bot administrators.", show_alert=True)
            return
        await query.message.reply_text(get_changelog_text(), parse_mode=ParseMode.HTML)
        return

    # 1. Open Admin Contact
    if data == "open_admin":
        keyboard = [
            [InlineKeyboardButton("💬 Message @RahilAnw4r", url=config.ADMIN_LINK)]
        ]
        await query.message.reply_text(
            f"👑 <b>Admin & Developer Support</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• <b>Developer:</b> Rahil Anwar\n"
            f"• <b>Telegram:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>\n"
            f"• <b>GitHub:</b> <a href=\"https://github.com/rahilanw4r\">github.com/rahilanw4r</a>\n\n"
            "💬 <i>Have questions, suggestions, or found a broken link? Click below to chat directly!</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    # 2. Open Help
    if data == "open_help":
        await help_command(update, context)
        return

    # 3. Refresh Analytics Dashboard
    if data == "refresh_stats":
        user = update.effective_user
        if config.is_admin(user):
            text = analytics.format_admin_dashboard()
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Refresh Stats", callback_data="refresh_stats"),
                ]
            ]
        else:
            text = analytics.format_public_dashboard()
            keyboard = [
                [
                    InlineKeyboardButton("💬 Contact Admin", url=config.ADMIN_LINK),
                ]
            ]

        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        return

    # 5. Download Buttons: format -> dl:<action>:<session_id>
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "dl":
        return

    action_tag = parts[1]
    session_id = parts[2]

    session = url_sessions.get(session_id)
    if not session:
        await query.edit_message_text(
            "⚠️ <i>This download session has expired. Please paste the link again!</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Cancel button
    if action_tag == "can":
        url_sessions.pop(session_id, None)
        await query.edit_message_text("❌ <i>Download cancelled.</i>", parse_mode=ParseMode.HTML)
        return

    target_url = session["url"]
    platform = session["platform"]
    chat_id = update.effective_chat.id

    # Audio extraction (MP3)
    if action_tag == "aud":
        status_msg = await query.message.reply_text(
            f"⏳ <b>Extracting MP3 audio from {html.escape(platform)}...</b>",
            parse_mode=ParseMode.HTML,
        )
        await execute_download(
            chat_id=chat_id,
            status_msg=status_msg,
            target_url=target_url,
            platform=platform,
            action="audio",
            context=context,
        )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inform user that /mode and quality pickers have been permanently removed."""
    if update.effective_message:
        await update.effective_message.reply_text(
            "ℹ️ <b>Quality Picker Removed:</b> The <code>/mode</code> command and resolution buttons have been permanently removed.\n\n"
            "All media is now automatically downloaded directly in full original quality with zero clicks!",
            parse_mode=ParseMode.HTML,
        )


async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mp3 and /audio commands to directly extract MP3."""
    if not await check_user_auth(update, context):
        return

    message = update.message
    if not message:
        return

    text = message.text or ""
    urls = find_urls(text)
    if not urls:
        await message.reply_text(
            "💡 <b>Usage:</b> <code>/mp3 &lt;url&gt;</code>\n"
            "Example: <code>/mp3 https://youtu.be/dQw4w9WgXcQ</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target_url = urls[0]
    platform = detect_platform(target_url)

    status_msg = await message.reply_text(
        f"⏳ Extracting MP3 audio from <b>{html.escape(platform)}</b>...",
        parse_mode=ParseMode.HTML,
    )
    await execute_download(
        chat_id=message.chat.id,
        status_msg=status_msg,
        target_url=target_url,
        platform=platform,
        action="audio",
        context=context,
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop and /cancel - Stop current download tasks and clear pending sessions."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    chat_id = update.effective_chat.id

    # 1. Cancel active background download tasks
    cancelled = cancel_user_tasks(user.id)
    if chat_id != user.id:
        cancelled += cancel_user_tasks(chat_id)

    # 2. Clear any pending download sessions
    cleared_sessions = 0
    to_remove = [sid for sid, d in url_sessions.items() if d.get("user_id") in (user.id, chat_id)]
    for sid in to_remove:
        url_sessions.pop(sid, None)
        cleared_sessions += 1

    if cancelled > 0 or cleared_sessions > 0:
        text = (
            "🛑 <b>Downloads Stopped</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• ⏹️ <b>Active Tasks Cancelled:</b> <code>{cancelled}</code>\n"
            f"• 🗑️ <b>Pending Sessions Cleared:</b> <code>{cleared_sessions}</code>\n\n"
            "✅ All current activities have been stopped successfully.\n"
            "💡 <i>Send any media link or /start whenever you are ready!</i>"
        )
    else:
        text = (
            "🛑 <b>No Active Downloads</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "You have no downloads running or pending right now.\n\n"
            "💡 <i>Send any video, photo, or music link to begin!</i>"
        )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Help", callback_data="open_help"),
            InlineKeyboardButton("💬 Contact Admin", url=config.ADMIN_LINK),
        ]
    ])
    await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ping command: check bot response latency and server status."""
    if not await check_user_auth(update, context):
        return

    start_time = time.time()
    msg = await update.effective_message.reply_text("🏓 <i>Pinging...</i>", parse_mode=ParseMode.HTML)
    latency_ms = int((time.time() - start_time) * 1000)

    sem = get_download_semaphore()
    active_downloads = MAX_CONCURRENT_DOWNLOADS - sem._value if sem else 0

    ping_text = (
        "🏓 <b>Pong! Server Status: Operational</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ <b>Latency:</b> <code>{latency_ms} ms</code>\n"
        f"⏱️ <b>Uptime:</b> <code>{analytics.format_uptime()}</code>\n"
        f"🔄 <b>Active Downloads:</b> <code>{max(0, active_downloads)} / {MAX_CONCURRENT_DOWNLOADS}</code>\n"
        f"🤖 <b>Architecture:</b> Python 3 • yt-dlp • FFmpeg • Gallery-DL\n\n"
        f"<i>All download pipelines running smoothly.</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats", callback_data="refresh_stats"),
            InlineKeyboardButton("💬 Contact Admin", url=config.ADMIN_LINK),
        ]
    ])
    await msg.edit_text(ping_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clean and /clearcache - Admin command to purge temporary downloaded files."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    if not config.is_admin(user):
        await update.effective_message.reply_text(
            "⛔ <b>Access Denied:</b> This command is restricted to the bot admin.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await update.effective_message.reply_text("🧹 <i>Scanning for leftover cache files...</i>", parse_mode=ParseMode.HTML)

    download_dir = Path(downloader.download_dir)
    freed_bytes = 0
    deleted_files = 0
    deleted_dirs = 0

    if download_dir.exists():
        import shutil
        for item in download_dir.iterdir():
            try:
                if item.is_file():
                    freed_bytes += item.stat().st_size
                    item.unlink()
                    deleted_files += 1
                elif item.is_dir():
                    for sub in item.rglob("*"):
                        if sub.is_file():
                            freed_bytes += sub.stat().st_size
                    shutil.rmtree(item, ignore_errors=True)
                    deleted_dirs += 1
            except Exception as e:
                logger.warning(f"Could not remove {item}: {e}")

    freed_mb = freed_bytes / (1024 * 1024)
    await status_msg.edit_text(
        f"✅ <b>Cache Cleanup Complete!</b>\n\n"
        f"• 🗑️ <b>Files Deleted:</b> <code>{deleted_files}</code>\n"
        f"• 📁 <b>Folders Purged:</b> <code>{deleted_dirs}</code>\n"
        f"• 💾 <b>Disk Space Freed:</b> <code>{freed_mb:.2f} MB</code>",
        parse_mode=ParseMode.HTML,
    )


async def cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cookies - Admin diagnostic command to check YouTube and platform cookies status."""
    if not await check_user_auth(update, context):
        return

    user = update.effective_user
    if not config.is_admin(user):
        await update.message.reply_text(
            "⛔ <b>Access Denied:</b> This command is restricted to the bot admin.",
            parse_mode=ParseMode.HTML,
        )
        return

    st = config.get_cookies_status()
    api_st = config.get_api_server_info()
    loaded_icon = "✅ Active" if st.get("loaded") else "⚠️ Inactive / Missing"
    yt_login_icon = "🔑 Authenticated" if st.get("has_login") else "👤 Guest Session"
    ig_login_icon = "🔑 Authenticated" if st.get("has_ig_login") else ("👤 Guest Session" if st.get("instagram_cookies", 0) > 0 else "None")
    tb_login_icon = "🔑 Authenticated" if st.get("has_tb_login") else ("👤 Guest Session" if st.get("terabox_cookies", 0) > 0 else "Bypass")
    api_engine_icon = "🚀 Local Bot API Server" if api_st.get("is_local") else "☁️ Cloud Bot API (Default)"

    msg = (
        "🍪 <b>Platform & API Server Diagnostics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 <b>Telegram Engine:</b> {api_engine_icon}\n"
        f"• <b>Max Upload Limit:</b> <code>{api_st.get('max_filesize_mb', 50):.0f} MB</code>\n"
        f"• <b>Server Base URL:</b> <code>{html.escape(api_st.get('base_url', ''))}</code>\n"
        f"• <b>Auto-Split Large Videos:</b> <code>{'Enabled' if api_st.get('auto_split') else 'Disabled'}</code>\n\n"
        f"🍪 <b>Cookies Status:</b> {loaded_icon}\n"
        f"• <b>Details:</b> {html.escape(st.get('message', ''))}\n"
        f"• <b>Total Cookies:</b> <code>{st.get('total_cookies', 0)}</code>\n"
        f"• <b>YouTube:</b> <code>{st.get('youtube_cookies', 0)}</code> cookies ({yt_login_icon})\n"
        f"• <b>Instagram:</b> <code>{st.get('instagram_cookies', 0)}</code> cookies ({ig_login_icon})\n"
        f"• <b>TeraBox:</b> <code>{st.get('terabox_cookies', 0)}</code> cookies ({tb_login_icon})\n"
        f"• <b>Storage File:</b> <code>{html.escape(str(st.get('path') or 'None'))}</code>\n\n"
        "⚡ <b>Mobile Client Bypass:</b> YouTube downloads automatically use iOS/Android bypass when cookies expire.\n\n"
        "📤 <b>How to Update Cookies:</b>\n"
        "1. Export your cookies using the <b>'Get cookies.txt LOCALLY'</b> browser extension.\n"
        "2. Send or forward the <code>cookies.txt</code> file directly to this chat.\n"
        "3. The bot validates and activates it with zero downtime!"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def admin_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow admin to send cookies.txt directly to the bot to update authentication."""
    message = update.message
    if not message or not message.document:
        return

    user = update.effective_user
    if not config.is_admin(user):
        return

    doc = message.document
    filename = (doc.file_name or "").lower()

    if not (filename.endswith(".txt") or "cookie" in filename):
        return

    status_msg = await message.reply_text("⏳ <i>Processing cookies document...</i>", parse_mode=ParseMode.HTML)

    try:
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        content = file_bytes.decode("utf-8", errors="ignore")

        if not ("\t" in content or "youtube" in content or "Netscape" in content):
            await status_msg.edit_text(
                "❌ <b>Invalid Cookie File:</b> The file does not look like a valid Netscape cookie file.",
                parse_mode=ParseMode.HTML,
            )
            return

        success = config.save_cookies_file(content)
        if success:
            st = config.get_cookies_status()
            await status_msg.edit_text(
                f"✅ <b>Cookies Updated Successfully!</b>\n\n"
                f"• <b>Total Cookies:</b> <code>{st.get('total_cookies', 0)}</code>\n"
                f"• <b>YouTube Cookies:</b> <code>{st.get('youtube_cookies', 0)}</code>\n"
                f"• <b>Instagram Cookies:</b> <code>{st.get('instagram_cookies', 0)}</code>\n"
                f"• <b>Saved to:</b> <code>{html.escape(str(st.get('path')))}</code>\n\n"
                f"🎉 The bot is now using your updated cookies for all downloads!",
                parse_mode=ParseMode.HTML,
            )
        else:
            await status_msg.edit_text("❌ Failed to save cookies file.", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.exception("Error updating cookies from document")
        await status_msg.edit_text(
            f"❌ <b>Error processing cookies:</b> {html.escape(str(e))}",
            parse_mode=ParseMode.HTML,
        )


async def post_init(application: Application) -> None:
    """Register bot commands in Telegram's native command menu."""
    commands = [
        BotCommand("start", "Start the bot & main menu"),
        BotCommand("mp3", "Extract MP3 audio (192kbps)"),
        BotCommand("stop", "Stop active downloads & clear sessions"),
        BotCommand("ping", "Check bot latency & server status"),
        BotCommand("stats", "View download analytics"),
        BotCommand("help", "User manual & instructions"),
        BotCommand("about", "About bot & specifications"),
        BotCommand("admin", "Contact developer / support"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Telegram command menu successfully registered.")
    except Exception as e:
        logger.warning("Could not register Telegram command menu: %s", e)


class HealthHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler to satisfy cloud health checks and enable keep-alive pings."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - Universal Media Downloader Bot is running!\n")

    def log_message(self, format, *args):
        # Suppress periodic health check logs to keep terminal output clean
        pass


def start_health_server():
    """Start lightweight HTTP server in a daemon thread for cloud hosting (e.g. Render)."""
    port = int(os.environ.get("PORT", "8080"))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("Health check server active on port %d", port)
    except Exception as e:
        logger.warning("Could not start health check server on port %d: %s", port, e)


def main():
    """Start the Telegram Downloader Bot."""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        print("❌ Error: TELEGRAM_BOT_TOKEN is not set in .env!")
        return

    # Start health check server for cloud hosting platforms (Render, Koyeb, etc.)
    start_health_server()

    print("🚀 Initializing Universal Media Downloader Bot...")
    builder = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .read_timeout(600)
        .write_timeout(600)
        .connect_timeout(60)
        .pool_timeout(60)
        .post_init(post_init)
    )

    if config.LOCAL_BOT_API_URL:
        base_url = config.LOCAL_BOT_API_URL.rstrip("/")
        if not base_url.endswith("/bot"):
            bot_base = f"{base_url}/bot"
            file_base = f"{base_url}/file/bot"
        else:
            bot_base = base_url
            file_base = base_url.replace("/bot", "/file/bot")
        builder = builder.base_url(bot_base).base_file_url(file_base)
        if config.LOCAL_BOT_API_LOCAL_MODE:
            builder = builder.local_mode(True)
        print(f"🌐 Local Bot API Server active: {bot_base} (Max file size: {config.MAX_FILE_SIZE_MB:.0f} MB, local_mode={config.LOCAL_BOT_API_LOCAL_MODE})")
    else:
        print(f"☁️ Cloud Telegram API active (Standard {config.MAX_FILE_SIZE_MB:.0f} MB upload limit).")

    app = builder.build()

    # Core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("contact", admin_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("changelog", changelog_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("analytics", stats_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("cancel", stop_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("clean", clean_command))
    app.add_handler(CommandHandler("clearcache", clean_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("cookies", cookies_command))
    app.add_handler(CommandHandler("cookie", cookies_command))
    app.add_handler(CommandHandler("mp3", audio_command))
    app.add_handler(CommandHandler("audio", audio_command))
    app.add_handler(CommandHandler("mode", mode_command))

    # Buttons callback
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    # Admin document handler (e.g. cookies.txt file uploads)
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, admin_document_handler))

    # All text messages with URLs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print(f"✅ Bot is running! Admin: {config.ADMIN_USERNAME}")
    print("🤖 Polling Telegram updates...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
