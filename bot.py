import os
import sys
import html
import time
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
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
                f"Your User ID is: <code>{user.id}</code>",
                parse_mode=ParseMode.HTML,
            )
        return False
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not await check_user_auth(update):
        return

    user = update.effective_user
    welcome_text = (
        f"👋 <b>Welcome, {html.escape(user.first_name)}!</b>\n\n"
        f"I am your <b>Universal Media Downloader</b> 📥\n"
        f"Send me any link to download videos, photos, carousels, or audio!\n\n"
        f"<b>Supported Platforms:</b>\n"
        f"• 📸 Instagram (Reels, Posts, Carousels)\n"
        f"• 🎥 YouTube (Shorts & Full HD Videos)\n"
        f"• 🐦 X / Twitter (Videos, Photos & Multi-Images)\n"
        f"• 🎵 TikTok (Clean, No Watermark)\n"
        f"• 📌 Pinterest (Image Pins & HD Video Pins)\n"
        f"• 🤖 Reddit, Facebook, & 1000+ other sites\n\n"
        f"<b>How to use:</b>\n"
        f"• Simply <b>paste any link</b> into the chat.\n"
        f"• Send <code>/mp3 &lt;link&gt;</code> to extract MP3 audio.\n"
        f"• Add me to any group chat for automatic link downloads!"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not await check_user_auth(update):
        return

    help_text = (
        "📖 <b>Universal Downloader — Quick Guide</b>\n\n"
        "<b>1. Download Video or Post:</b>\n"
        "Paste any public link. The bot automatically downloads videos, photos, or multi-item albums.\n\n"
        "<b>2. Extract MP3 Audio:</b>\n"
        "Send <code>/mp3 &lt;link&gt;</code> or <code>/audio &lt;link&gt;</code> to download 192k MP3 audio with official album art.\n\n"
        "<b>3. Group Chats:</b>\n"
        "Add this bot to your group chat and it will automatically download any video/post link shared by members.\n\n"
        "<b>Commands:</b>\n"
        "• /start - Welcome message & status\n"
        "• /help - Show this guide\n"
        "• /mp3 &lt;url&gt; - Extract MP3 audio"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def execute_download(
    chat_id: int,
    status_msg,
    target_url: str,
    platform: str,
    action: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Perform download with live progress bar and deliver media to Telegram."""
    loop = asyncio.get_running_loop()
    tracker = ProgressTracker(status_msg, loop, platform)

    if action == "media":
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        try:
            result = await downloader.download_media(
                url=target_url,
                progress_callback=tracker.on_progress,
            )
            media_type = result.get("type", "video")
            dir_path = result.get("dir_path")
            title = result.get("title", "Media")
            filesize_mb = result.get("filesize_mb", 0)
            bot_handle = f"@{context.bot.username}" if (context.bot and context.bot.username) else ""

            # 1. Single Photo delivery
            if media_type == "photo":
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                file_path = result["file_path"]
                caption = (
                    f"🖼️ <b>{html.escape(title[:100])}</b>\n"
                    f"📁 Source: {html.escape(platform)} ({filesize_mb:.1f} MB)"
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
                                caption=f"🖼️ {title[:100]}\n📁 Source: {platform} ({filesize_mb:.1f} MB)",
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
                    f"📁 Source: {html.escape(platform)} ({len(file_paths)} items)"
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
                                    item.caption = f"📸 {title[:100]}\n📁 Source: {platform} ({len(file_paths)} items)"
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
                caption = (
                    f"🎬 <b>{html.escape(title[:100])}</b>\n"
                    f"📁 Source: {html.escape(platform)} ({filesize_mb:.1f} MB)"
                )
                if bot_handle:
                    caption += f"\n🤖 {html.escape(bot_handle)}"

                with open(file_path, "rb") as f:
                    try:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=f,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            supports_streaming=True,
                            read_timeout=300,
                            write_timeout=300,
                        )
                    except Exception as e:
                        if "parse entities" in str(e).lower():
                            f.seek(0)
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=f,
                                caption=f"🎬 {title[:100]}\n📁 Source: {platform} ({filesize_mb:.1f} MB)",
                                supports_streaming=True,
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
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Download Failed:\n{e}",
            )
        except Exception as e:
            logger.exception("Unexpected error sending media")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error uploading media: {e}",
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

            caption = f"🎵 <b>{html.escape(title[:100])}</b> ({filesize_mb:.1f} MB)"
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
                            caption=f"🎵 {title[:100]} ({filesize_mb:.1f} MB)",
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
                text=f"❌ Audio Extraction Failed:\n{e}",
            )
        except Exception as e:
            logger.exception("Unexpected error sending audio")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error uploading audio: {e}",
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
        return

    target_url = urls[0]
    platform = detect_platform(target_url)

    status_msg = await message.reply_text(
        f"⏳ Fetching media from <b>{html.escape(platform)}</b>...",
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
    app.add_handler(CommandHandler("mp3", audio_command))
    app.add_handler(CommandHandler("audio", audio_command))

    # All text messages with URLs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("✅ Bot is running! Polling Telegram...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
