import sys
import html
import time
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction, ChatType, ParseMode
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
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Initialize Downloader instance
downloader = MediaDownloader()

# In-memory session store for download actions
# Format: {session_id: {"url": url, "platform": platform, "user_id": user_id, "title": title}}
url_sessions: Dict[str, Dict[str, Any]] = {}


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

        # Update at most once every 1.6 seconds to avoid Telegram 429 rate limits
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
            f"⏳ *Downloading from {self.platform}...*\n\n"
            f"`{bar}`\n"
            f"⚡ *Speed:* {speed_mb:.1f} MB/s\n"
            f"📦 *Size:* {dl_mb:.1f} MB / {total_mb:.1f} MB\n"
            f"⏱️ *ETA:* {int(eta)}s"
        )

        async def _edit():
            try:
                await self.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
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
                "⛔ *Access Denied*: You are not authorized to use this bot.\n"
                f"Your Telegram User ID is: `{user.id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        elif update.callback_query:
            await update.callback_query.answer("⛔ Access denied.", show_alert=True)
        return False
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not await check_user_auth(update):
        return

    user = update.effective_user
    user_id = user.id
    current_mode = config.get_user_setting(user_id, "mode", "interactive")
    is_instant = (current_mode == "instant")
    mode_badge = "⚡ Fast Instant Download (Zero Clicks)" if is_instant else "🔘 Ask Quality Every Time (Menu)"

    welcome_text = (
        f"🌟 *Welcome, {html.escape(user.first_name)}!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"I am your *All-in-One Social Media Downloader* 📥\n\n"
        f"🚀 *What you can download:*\n"
        f"• 📸 *Instagram* (Reels, Posts, Stories)\n"
        f"• 🎥 *YouTube* (Shorts, HD Videos, MP3 Music)\n"
        f"• 🐦 *X / Twitter* (Videos & GIFs)\n"
        f"• 🎵 *TikTok* (Clean, No Watermark)\n"
        f"• 📌 *Pinterest* (Videos & Clips)\n"
        f"• 🤖 *Reddit* & *Facebook* Videos\n\n"
        f"🎬 *How Video Quality Works:*\n"
        f"Currently set to: `{mode_badge}`\n\n"
        f"• *By Default:* Whenever you paste a link, I ask which quality you want:\n"
        f"  `1080p Full HD` • `720p HD` • `480p Data Saver` • `🎵 MP3 Audio`\n\n"
        f"• *Want Faster Downloads?*\n"
        f"  Use `/quality` or `/settings` to turn on *⚡ Fast Instant Mode*.\n"
        f"  In Fast Mode, you paste a link and get the video immediately with *zero clicks*!\n\n"
        f"👥 *Works in Group Chats:*\n"
        f"Add me to any group with friends, and I will automatically download any video link shared in the chat!\n\n"
        f"👇 *Send any link now, or change your settings below:*"
    )

    keyboard = [
        [
            InlineKeyboardButton("⚙️ Change Quality / Mode", callback_data="open_settings"),
            InlineKeyboardButton("📖 User Guide", callback_data="open_help"),
        ]
    ]

    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not await check_user_auth(update):
        return

    help_text = (
        "📖 *All-in-One Downloader — Quick Guide*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎛️ *1. How to Change Video Quality / Mode:*\n"
        "Send `/quality` or `/settings` to choose how you want videos delivered:\n"
        "• *Ask Quality Every Time:* Gives you buttons for 1080p, 720p, 480p, or MP3.\n"
        "• *Fast Instant Download:* Directly downloads the best quality as soon as you drop a link.\n\n"
        "🎵 *2. Downloading Audio Only (MP3):*\n"
        "When you send a link, tap the *🎵 MP3 Audio* button to get the audio with official album art in Telegram's music player.\n\n"
        "👥 *3. Using in Group Chats:*\n"
        "Add @UniversalMediaSaverBot to any Telegram group chat. Whenever someone posts an Instagram Reel or YouTube link, the bot automatically downloads it right inside the group!\n\n"
        "📌 *Commands:*\n"
        "• `/start` - Welcome message & status\n"
        "• `/quality` - Change quality & instant download mode\n"
        "• `/settings` - Bot settings & download preferences\n"
        "• `/help` - Show this guide"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings and /quality command."""
    if not await check_user_auth(update):
        return

    user_id = update.effective_user.id
    current_mode = config.get_user_setting(user_id, "mode", "interactive")

    is_instant = (current_mode == "instant")
    mode_status = "⚡ *Fast Instant Mode* (Sends video with zero clicks)" if is_instant else "🔘 *Ask Quality Mode* (Choose 1080p, 720p, 480p, MP3)"

    button_label = "👉 Switch to 🔘 Ask Quality Mode" if is_instant else "👉 Switch to ⚡ Fast Instant Mode"

    keyboard = [
        [
            InlineKeyboardButton(button_label, callback_data="toggle_mode")
        ],
        [
            InlineKeyboardButton("🔙 Close", callback_data="close_settings"),
        ],
    ]

    text = (
        f"⚙️ *Video Quality & Download Settings*\n\n"
        f"Current Mode: {mode_status}\n\n"
        f"• *🔘 Ask Quality Every Time:*\n"
        f"  Shows buttons so you can pick `1080p FHD`, `720p HD`, `480p Data Saver`, or `MP3 Audio`.\n\n"
        f"• *⚡ Fast Instant Mode:*\n"
        f"  Pastes link ➡️ Video delivers immediately with zero extra clicks!\n\n"
        f"Tap below to switch:"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


async def execute_download(
    chat_id: int,
    status_msg,
    target_url: str,
    platform: str,
    action: str,
    resolution: Optional[int],
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Perform download with live progress bar and deliver to Telegram."""
    loop = asyncio.get_running_loop()
    tracker = ProgressTracker(status_msg, loop, platform)

    if action == "video":
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        try:
            result = await downloader.download_video(
                url=target_url,
                resolution=resolution,
                progress_callback=tracker.on_progress,
            )
            file_path = result["file_path"]
            dir_path = result["dir_path"]
            title = result.get("title", "Video")
            filesize_mb = result.get("filesize_mb", 0)

            try:
                await status_msg.edit_text(
                    f"⬆️ *Uploading video ({filesize_mb:.1f} MB) to Telegram...*\nAlmost done!",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

            bot_handle = f"@{context.bot.username}" if context.bot.username else "@UniversalMediaSaverBot"
            caption = (
                f"🎬 *{html.escape(title[:100])}*\n"
                f"📁 Source: {platform} ({filesize_mb:.1f} MB)\n"
                f"🤖 Downloaded via {bot_handle}"
            )

            with open(file_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True,
                    read_timeout=300,
                    write_timeout=300,
                )

            # Cleanup temp files
            downloader.cleanup(dir_path)
            try:
                await status_msg.delete()
            except Exception:
                pass

        except DownloaderError as e:
            logger.error(f"Download error: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ *Download Failed:*\n{e}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.exception("Unexpected error sending video")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ *Error uploading video:* {e}",
                parse_mode=ParseMode.MARKDOWN,
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

            try:
                await status_msg.edit_text(
                    f"⬆️ *Uploading MP3 ({filesize_mb:.1f} MB) to Telegram...*",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

            bot_handle = f"@{context.bot.username}" if context.bot.username else "@UniversalMediaSaverBot"

            thumb_handle = None
            if thumb_file and Path(thumb_file).exists():
                thumb_handle = open(thumb_file, "rb")

            try:
                with open(file_path, "rb") as f:
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=f,
                        thumbnail=thumb_handle,
                        title=title,
                        performer=uploader,
                        duration=duration,
                        caption=f"🎵 *{html.escape(title[:100])}*\n🤖 {bot_handle}",
                        parse_mode=ParseMode.MARKDOWN,
                        read_timeout=300,
                        write_timeout=300,
                    )
            finally:
                if thumb_handle:
                    thumb_handle.close()

            # Cleanup temp files
            downloader.cleanup(dir_path)
            try:
                await status_msg.delete()
            except Exception:
                pass

        except DownloaderError as e:
            logger.error(f"Audio download error: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ *Audio Extraction Failed:*\n{e}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.exception("Unexpected error sending audio")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ *Error uploading audio:* {e}",
                parse_mode=ParseMode.MARKDOWN,
            )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages with URLs in private chats and group chats."""
    if not await check_user_auth(update):
        return

    message = update.message
    if not message or not message.text:
        return

    urls = find_urls(message.text)
    if not urls:
        # In private chat, remind user to send link
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_text(
                "💡 Paste any video or post link (Instagram, YouTube, Twitter/X, TikTok, Pinterest, etc.) to download!"
            )
        return

    target_url = urls[0]
    platform = detect_platform(target_url)
    chat_type = message.chat.type
    user_id = update.effective_user.id

    # 👥 GROUP CHAT LOGIC: Auto-download without prompting buttons for smooth group chat UX
    if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
        status_msg = await message.reply_text(
            f"⏳ Fetching video from *{platform}*...",
            parse_mode=ParseMode.MARKDOWN,
        )
        await execute_download(
            chat_id=message.chat.id,
            status_msg=status_msg,
            target_url=target_url,
            platform=platform,
            action="video",
            resolution=None,
            context=context,
        )
        return

    # 📱 PRIVATE CHAT LOGIC: Check user mode (Instant vs Interactive)
    user_mode = config.get_user_setting(user_id, "mode", "interactive")

    if user_mode == "instant":
        status_msg = await message.reply_text(
            f"⏳ Starting instant download from *{platform}*...",
            parse_mode=ParseMode.MARKDOWN,
        )
        await execute_download(
            chat_id=message.chat.id,
            status_msg=status_msg,
            target_url=target_url,
            platform=platform,
            action="video",
            resolution=None,
            context=context,
        )
        return

    # 🎛️ INTERACTIVE MODE: Extract resolutions and show options
    loading_msg = await message.reply_text("🔍 Analyzing media options...")

    try:
        media_opts = await downloader.extract_media_options(target_url)
    except Exception as e:
        logger.warning(f"Failed to inspect formats, using fallback: {e}")
        media_opts = {
            "title": "Media",
            "qualities": [{"height": 0, "label": "🎬 Download Video", "code": "best"}],
        }

    session_id = str(uuid.uuid4())[:8]
    url_sessions[session_id] = {
        "url": target_url,
        "platform": platform,
        "user_id": user_id,
        "title": media_opts.get("title", "Video"),
    }

    # Build resolution buttons
    keyboard = []
    qualities = media_opts.get("qualities", [])

    # Group video qualities in rows of 2
    row = []
    for q in qualities:
        h = q.get("height", 0)
        label = q.get("label", "Video")
        cb_data = f"dl:vid_{h}:{session_id}"
        row.append(InlineKeyboardButton(label, callback_data=cb_data))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Audio button & Cancel button
    keyboard.append([
        InlineKeyboardButton("🎵 MP3 Audio (Cover Art)", callback_data=f"dl:aud:{session_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"dl:can:{session_id}"),
    ])

    title_clean = media_opts.get("title", "")[:50]
    display_text = (
        f"🎬 *{html.escape(title_clean)}*\n"
        f"🔗 Source: *{platform}*\n\n"
        f"Select your preferred download format:"
    )

    await loading_msg.edit_text(
        display_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle clicks on resolution, audio, and settings buttons."""
    query = update.callback_query
    if not query or not query.data:
        return

    if not await check_user_auth(update):
        return

    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    # Handle /settings toggle
    if data == "toggle_mode":
        current_mode = config.get_user_setting(user_id, "mode", "interactive")
        new_mode = "instant" if current_mode == "interactive" else "interactive"
        config.set_user_setting(user_id, "mode", new_mode)

        is_instant = (new_mode == "instant")
        mode_status = "⚡ *Instant Mode* (Fastest, zero clicks)" if is_instant else "🔘 *Interactive Mode* (Quality Picker)"

        keyboard = [
            [
                InlineKeyboardButton(
                    "Switch to " + ("🔘 Interactive" if is_instant else "⚡ Instant Mode"),
                    callback_data="toggle_mode",
                )
            ],
            [
                InlineKeyboardButton("🔙 Close", callback_data="close_settings"),
            ],
        ]

        await query.edit_message_text(
            f"✅ Settings updated!\n\n"
            f"Current Mode: {mode_status}\n\n"
            f"• *Interactive:* Shows resolution buttons (1080p/720p/480p) or MP3.\n"
            f"• *Instant:* Immediately downloads and sends the video upon link drop.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "open_settings":
        current_mode = config.get_user_setting(user_id, "mode", "interactive")
        is_instant = (current_mode == "instant")
        mode_status = "⚡ *Instant Mode* (Fastest, zero clicks)" if is_instant else "🔘 *Interactive Mode* (Quality Picker)"
        keyboard = [
            [
                InlineKeyboardButton(
                    "Switch to " + ("🔘 Interactive" if is_instant else "⚡ Instant Mode"),
                    callback_data="toggle_mode",
                )
            ],
            [
                InlineKeyboardButton("🔙 Close", callback_data="close_settings"),
            ],
        ]
        await query.message.reply_text(
            f"⚙️ *Download Settings*\n\n"
            f"Current Mode: {mode_status}\n\n"
            f"• *Interactive:* Asks you to pick video resolution (1080p/720p/480p) or MP3.\n"
            f"• *Instant:* Instantly downloads the best quality as soon as you paste a link.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "open_help":
        help_text = (
            "📖 *All-in-One Downloader — User Manual*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚡ *1. What is Instant Mode?*\n"
            "• *Interactive Mode (Default):* Displays buttons for resolutions (1080p, 720p, 480p, 360p) or MP3.\n"
            "• *Instant Mode:* Skips all menus! Paste a link and your video is downloaded and delivered immediately.\n"
            "👉 *To toggle:* Send `/settings` anytime!\n\n"
            "🎵 *2. Studio MP3 with Album Art:*\n"
            "Tap the *MP3 Audio* button on any link to get an audio track complete with the official thumbnail cover art in Telegram's player.\n\n"
            "👥 *3. Using in Group Chats:*\n"
            "1. Add @UniversalMediaSaverBot to your group.\n"
            "2. Any group member drops a link ➡️ Bot automatically replies with the video directly in the group!\n\n"
            "⚠️ *Telegram Limits:*\n"
            "Files up to *50 MB* are delivered instantly."
        )
        await query.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
        return

    if data == "close_settings":
        try:
            await query.delete_message()
        except Exception:
            pass
        return

    # Handle download buttons (format: dl:<action>:<session_id>)
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "dl":
        return

    action_tag = parts[1]
    session_id = parts[2]

    session = url_sessions.get(session_id)
    if not session:
        await query.edit_message_text("⚠️ This download session has expired. Please paste the link again.")
        return

    if action_tag == "can":
        url_sessions.pop(session_id, None)
        await query.edit_message_text("❌ Download cancelled.")
        return

    target_url = session["url"]
    platform = session["platform"]
    chat_id = update.effective_chat.id

    if action_tag.startswith("vid_"):
        res_str = action_tag.split("_")[1]
        resolution = int(res_str) if res_str.isdigit() and int(res_str) > 0 else None
        res_label = f"{resolution}p" if resolution else "Best"

        await query.edit_message_text(
            f"⏳ *Starting {res_label} download from {platform}...*\nConnecting...",
            parse_mode=ParseMode.MARKDOWN,
        )
        url_sessions.pop(session_id, None)
        await execute_download(
            chat_id=chat_id,
            status_msg=query.message,
            target_url=target_url,
            platform=platform,
            action="video",
            resolution=resolution,
            context=context,
        )

    elif action_tag == "aud":
        await query.edit_message_text(
            f"⏳ *Extracting MP3 audio from {platform}...*\nConnecting...",
            parse_mode=ParseMode.MARKDOWN,
        )
        url_sessions.pop(session_id, None)
        await execute_download(
            chat_id=chat_id,
            status_msg=query.message,
            target_url=target_url,
            platform=platform,
            action="audio",
            resolution=None,
            context=context,
        )


def main():
    """Start the Telegram Downloader Bot."""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        print("=" * 60)
        print("❌ Error: TELEGRAM_BOT_TOKEN is not set in .env!")
        print("=" * 60)
        return

    print("🚀 Initializing Universal Media Downloader Bot with 5 Advanced Features...")
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("quality", settings_command))
    app.add_handler(CommandHandler("mode", settings_command))

    # Buttons callback
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    # Text messages with URLs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("✅ All-in-One Downloader Bot is running!")
    print("🤖 Polling Telegram... Send any link to @UniversalMediaSaverBot!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
