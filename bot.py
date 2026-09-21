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

# Active URL sessions for inline quality buttons and quick actions
url_sessions: Dict[str, Dict[str, Any]] = {}


def cleanup_expired_sessions() -> None:
    """Purge sessions older than 1 hour to prevent memory buildup."""
    now = time.time()
    expired = [sid for sid, data in url_sessions.items() if now - data.get("time", 0) > 3600]
    for sid in expired:
        url_sessions.pop(sid, None)


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


async def check_user_auth(update: Update) -> bool:
    """Validate if user is authorized to use the bot."""
    user = update.effective_user
    if not user:
        return False
    if not config.is_user_allowed(user.id):
        if update.message:
            await update.message.reply_text(
                f"⛔ <b>Access Denied</b>: You are not authorized to use this bot.\n"
                f"Your User ID is: <code>{user.id}</code>\n"
                f"Contact Admin: <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>",
                parse_mode=ParseMode.HTML,
            )
        return False
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with rich greeting, command directory, and quick buttons."""
    if not await check_user_auth(update):
        return

    user = update.effective_user
    current_mode = config.get_user_mode(user.id)
    mode_text = "⚡ Instant Auto-Download" if current_mode == "instant" else "🔘 Quality Selector (1080p/720p/480p)"

    bot_username = context.bot.username if (context.bot and context.bot.username) else ""
    add_group_url = f"https://t.me/{bot_username}?startgroup=true" if bot_username else None

    welcome_text = (
        f"👋 <b>Hello, {html.escape(user.first_name)}!</b>\n\n"
        f"Welcome to <b>Universal Media Downloader</b> 📥\n"
        f"Download videos, photos, carousels & music from <b>Instagram, YouTube, TikTok, X (Twitter), Pinterest, Reddit</b> & 1,000+ sites.\n\n"
        f"⚙️ <b>Active Mode:</b> <code>{mode_text}</code>\n\n"
        f"👥 <b>Works in Group Chats!</b>\n"
        f"Add me to any group chat and I'll automatically download and send every media link shared by members!\n\n"
        f"📌 <b>Quick Commands:</b>\n"
        f"• <b>Paste any link</b> — Auto-download media\n"
        f"• <code>/mode</code> — Toggle Instant vs Quality Picker\n"
        f"• <code>/mp3 &lt;link&gt;</code> — Extract 192kbps audio with cover art\n"
        f"• <code>/help</code> — Full guide & instructions\n"
        f"• <code>/admin</code> — Contact developer (@RahilAnw4r)\n\n"
        f"👑 <b>Developer:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>"
    )

    keyboard = []
    if add_group_url:
        keyboard.append([InlineKeyboardButton("➕ Add Me to Your Group", url=add_group_url)])

    keyboard.extend([
        [
            InlineKeyboardButton("⚡ Switch Mode", callback_data="toggle_mode"),
            InlineKeyboardButton("💬 Contact Admin", callback_data="open_admin"),
        ],
        [
            InlineKeyboardButton("📖 User Guide & Help", callback_data="open_help"),
        ],
    ])

    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not await check_user_auth(update):
        return

    help_text = (
        "📖 <b>Universal Downloader — User Manual</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 <b>How to Download:</b>\n"
        "1. Send or forward any public video or photo link.\n"
        "2. The bot delivers the media with a live animated progress bar.\n\n"
        "⚡ <b>Download Modes (<code>/mode</code>):</b>\n"
        "• <b>⚡ Instant Mode:</b> Fastest delivery. Best quality downloads automatically without asking.\n"
        "• <b>🔘 Quality Picker:</b> Shows resolution buttons (1080p, 720p, 480p, MP3) to choose from.\n\n"
        "🎵 <b>Audio Extraction (<code>/mp3</code>):</b>\n"
        "• Send <code>/mp3 &lt;link&gt;</code> to extract 192k audio with official album art.\n"
        "• Or tap <b>Extract MP3</b> directly under any sent video!\n\n"
        "👥 <b>Group Chats:</b>\n"
        "Add this bot to any group chat. When any member shares a link, the bot delivers the media directly into the group.\n\n"
        "📌 <b>All Commands:</b>\n"
        "• <code>/start</code> — Greeting, status & quick buttons\n"
        "• <code>/mode</code> — Switch Instant vs Quality Picker\n"
        "• <code>/mp3 &lt;url&gt;</code> — Extract MP3 audio\n"
        "• <code>/admin</code> — Contact developer directly\n"
        "• <code>/about</code> — Bot technical specifications\n"
        "• <code>/help</code> — Show this manual\n\n"
        f"👑 <b>Developer:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>"
    )

    keyboard = [
        [
            InlineKeyboardButton("⚡ Switch Mode", callback_data="toggle_mode"),
            InlineKeyboardButton("💬 Chat with Admin", url=config.ADMIN_LINK),
        ]
    ]

    await update.message.reply_text(
        help_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mode command: quickly flip between Instant Mode and Quality Picker."""
    if not await check_user_auth(update):
        return

    user_id = update.effective_user.id
    current_mode = config.get_user_mode(user_id)

    # Support optional explicit argument: /mode instant or /mode picker
    args = context.args or []
    if args:
        requested = args[0].lower().strip()
        if requested in ("instant", "fast", "auto"):
            new_mode = "instant"
        elif requested in ("picker", "ask", "quality", "interactive"):
            new_mode = "picker"
        else:
            new_mode = "picker" if current_mode == "instant" else "instant"
    else:
        new_mode = "picker" if current_mode == "instant" else "instant"

    config.set_user_mode(user_id, new_mode)
    is_instant = (new_mode == "instant")
    toggle_label = "Switch to 🔘 Quality Picker" if is_instant else "Switch to ⚡ Instant Mode"

    if is_instant:
        mode_text = (
            "⚙️ <b>Download Mode: ⚡ Instant Mode</b>\n\n"
            "✅ <b>Active!</b> Links will now download immediately in highest available quality with zero extra clicks.\n\n"
            "💡 <i>Tap below or send <code>/mode</code> anytime to switch to Quality Picker.</i>"
        )
    else:
        mode_text = (
            "⚙️ <b>Download Mode: 🔘 Quality Picker</b>\n\n"
            "✅ <b>Active!</b> When you send a video link, the bot will display buttons to pick 1080p, 720p, 480p, or MP3.\n\n"
            "💡 <i>Tap below or send <code>/mode</code> anytime to switch to Instant Mode.</i>"
        )

    keyboard = [
        [
            InlineKeyboardButton(toggle_label, callback_data="toggle_mode"),
        ]
    ]

    await update.message.reply_text(
        mode_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin and /contact commands."""
    if not await check_user_auth(update):
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
    if not await check_user_auth(update):
        return

    about_text = (
        "🤖 <b>Universal Media Downloader</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• <b>Version:</b> 2.1.0 (Cloud Edition)\n"
        "• <b>Architecture:</b> Python 3.11 • yt-dlp • FFmpeg • Gallery-DL\n"
        "• <b>Hosting:</b> 24/7 Cloud Active\n"
        f"• <b>Developer:</b> <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>\n\n"
        "High-performance media extractor built for speed, quality, and simplicity."
    )

    keyboard = [
        [
            InlineKeyboardButton("💬 Contact Admin", url=config.ADMIN_LINK),
            InlineKeyboardButton("⚡ Switch Mode", callback_data="toggle_mode"),
        ]
    ]

    await update.message.reply_text(
        about_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def execute_download(
    chat_id: int,
    status_msg,
    target_url: str,
    platform: str,
    action: str,
    context: ContextTypes.DEFAULT_TYPE,
    resolution: Optional[int] = None,
) -> None:
    """Perform download with live progress bar and deliver media to Telegram."""
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

                with open(file_path, "rb") as f:
                    try:
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=f,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            read_timeout=300,
                            write_timeout=300,
                        )
                    except Exception as e:
                        if "parse entities" in str(e).lower():
                            f.seek(0)
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=f,
                                caption=f"🖼️ {title[:100]}\n📁 {platform} • 📦 {filesize_mb:.1f} MB",
                                read_timeout=300,
                                write_timeout=300,
                            )
                        else:
                            raise

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
                        if "parse entities" in str(e).lower():
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
                        else:
                            raise
                finally:
                    for fh in open_files:
                        fh.close()

            # 3. Single Video delivery
            else:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                file_path = result["file_path"]

                dur_str = f" • ⏱️ {int(duration)}s" if duration else ""
                res_str = f" ({resolution}p)" if resolution else ""
                caption = (
                    f"🎬 <b>{html.escape(title[:100])}</b>\n"
                    f"📁 {html.escape(platform)}{res_str} • 📦 {filesize_mb:.1f} MB{dur_str}"
                )
                if bot_handle:
                    caption += f"\n🤖 {html.escape(bot_handle)}"

                # Quick action buttons: One-tap MP3 extraction or mode switch
                post_keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🎵 Extract MP3 Audio", callback_data=f"dl:aud:{vid_session_id}"),
                        InlineKeyboardButton("⚡ Mode", callback_data="toggle_mode"),
                    ]
                ])

                with open(file_path, "rb") as f:
                    try:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=f,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            supports_streaming=True,
                            reply_markup=post_keyboard,
                            read_timeout=300,
                            write_timeout=300,
                        )
                    except Exception as e:
                        if "parse entities" in str(e).lower():
                            f.seek(0)
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=f,
                                caption=f"🎬 {title[:100]}\n📁 {platform}{res_str} • 📦 {filesize_mb:.1f} MB{dur_str}",
                                supports_streaming=True,
                                reply_markup=post_keyboard,
                                read_timeout=300,
                                write_timeout=300,
                            )
                        else:
                            raise

            # Cleanup temp files
            if dir_path:
                downloader.cleanup(dir_path)
            try:
                await status_msg.delete()
            except Exception:
                pass

        except DownloaderError as e:
            logger.error(f"Download error: {e}")
            error_text = (
                f"❌ <b>Download Failed:</b>\n{html.escape(str(e))}\n\n"
                f"💬 <i>Need help? Contact developer <a href=\"{config.ADMIN_LINK}\">{config.ADMIN_USERNAME}</a>.</i>"
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=error_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.exception("Unexpected error sending media")
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ <b>Error uploading media:</b> {html.escape(str(e))}\n\n"
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
                            read_timeout=300,
                            write_timeout=300,
                        )
                else:
                    raise
            finally:
                if thumb_handle:
                    thumb_handle.close()

            # Cleanup temp files
            if dir_path:
                downloader.cleanup(dir_path)
            try:
                await status_msg.delete()
            except Exception:
                pass

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
    """Handle links based on user preference (Instant download vs Interactive Quality Picker)."""
    if not await check_user_auth(update):
        return

    message = update.message
    if not message or not message.text:
        return

    urls = find_urls(message.text)
    if not urls:
        return

    target_url = urls[0]
    platform = detect_platform(target_url)
    user_id = update.effective_user.id
    current_mode = config.get_user_mode(user_id)

    # 1. Instant Mode: Download immediately with zero friction
    if current_mode == "instant":
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
        return

    # 2. Quality Picker Mode: Analyze link and show quality selection buttons
    status_msg = await message.reply_text(
        f"🔍 <b>Analyzing media from {html.escape(platform)}...</b>",
        parse_mode=ParseMode.HTML,
    )

    try:
        options = await downloader.extract_media_options(target_url)
    except Exception as e:
        logger.warning(f"Failed to extract media options, defaulting to instant: {e}")
        await execute_download(
            chat_id=message.chat.id,
            status_msg=status_msg,
            target_url=target_url,
            platform=platform,
            action="media",
            context=context,
        )
        return

    session_id = uuid.uuid4().hex[:8]
    url_sessions[session_id] = {
        "url": target_url,
        "platform": platform,
        "time": time.time(),
        "user_id": user_id,
    }
    cleanup_expired_sessions()

    # Build resolution buttons
    qualities = options.get("qualities", [])
    keyboard = []
    row = []

    for q in qualities:
        label = q.get("label", "Download")
        code = q.get("code", "res_best")
        row.append(InlineKeyboardButton(label, callback_data=f"dl:vid_{code}:{session_id}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Add MP3 and Cancel buttons
    keyboard.append([
        InlineKeyboardButton("🎵 MP3 Audio (192k)", callback_data=f"dl:aud:{session_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"dl:can:{session_id}"),
    ])

    title = options.get("title", "Media")[:60]
    uploader = options.get("uploader", platform)
    duration = options.get("duration", 0)
    dur_str = f" • ⏱️ {int(duration)}s" if duration else ""

    display_text = (
        f"🎬 <b>{html.escape(title)}</b>\n"
        f"👤 {html.escape(uploader)}{dur_str} | Source: <b>{html.escape(platform)}</b>\n\n"
        f"<i>Select your preferred download format:</i>"
    )

    await status_msg.edit_text(
        display_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle resolution picker, MP3 extraction, mode toggle, and admin buttons."""
    query = update.callback_query
    if not query or not query.data:
        return

    if not await check_user_auth(update):
        return

    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    # 1. Toggle Mode Setting
    if data == "toggle_mode":
        current_mode = config.get_user_mode(user_id)
        new_mode = "picker" if current_mode == "instant" else "instant"
        config.set_user_mode(user_id, new_mode)

        is_instant = (new_mode == "instant")
        toggle_label = "Switch to 🔘 Quality Picker" if is_instant else "Switch to ⚡ Instant Mode"

        if is_instant:
            text = (
                "⚙️ <b>Download Mode: ⚡ Instant Mode</b>\n\n"
                "✅ <b>Active!</b> Links will now download immediately in highest available quality with zero extra clicks.\n\n"
                "💡 <i>Tap below or send <code>/mode</code> anytime to switch to Quality Picker.</i>"
            )
        else:
            text = (
                "⚙️ <b>Download Mode: 🔘 Quality Picker</b>\n\n"
                "✅ <b>Active!</b> When you send a video link, the bot will display buttons to pick 1080p, 720p, 480p, or MP3.\n\n"
                "💡 <i>Tap below or send <code>/mode</code> anytime to switch to Instant Mode.</i>"
            )

        keyboard = [
            [InlineKeyboardButton(toggle_label, callback_data="toggle_mode")],
        ]

        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            await query.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        return

    # 2. Open Admin Contact
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

    # 3. Open Help
    if data == "open_help":
        await help_command(update, context)
        return

    # 4. Download Buttons: format -> dl:<action>:<session_id>
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

    # Video download with specified resolution
    if action_tag.startswith("vid_"):
        raw_code = action_tag.replace("vid_", "")
        resolution = None
        if "res_" in raw_code:
            res_num = raw_code.replace("res_", "")
            if res_num.isdigit() and int(res_num) > 0:
                resolution = int(res_num)

        res_label = f"{resolution}p" if resolution else "Best Quality"

        status_msg = await query.message.reply_text(
            f"⏳ <b>Starting {res_label} download from {html.escape(platform)}...</b>",
            parse_mode=ParseMode.HTML,
        )
        try:
            await query.delete_message()
        except Exception:
            pass

        await execute_download(
            chat_id=chat_id,
            status_msg=status_msg,
            target_url=target_url,
            platform=platform,
            action="media",
            context=context,
            resolution=resolution,
        )

    # Audio extraction (MP3)
    elif action_tag == "aud":
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


async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mp3 and /audio commands to directly extract MP3."""
    if not await check_user_auth(update):
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
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )

    # Core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("contact", admin_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("mp3", audio_command))
    app.add_handler(CommandHandler("audio", audio_command))

    # Buttons callback
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    # All text messages with URLs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print(f"✅ Bot is running! Admin: {config.ADMIN_USERNAME}")
    print("🤖 Polling Telegram updates...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
