import asyncio
import os
import re
import shutil
import tempfile
import logging
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from PIL import Image
import imageio_ffmpeg
import yt_dlp

logger = logging.getLogger(__name__)

# Resolve embedded ffmpeg binary
try:
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception as e:
    logger.warning(f"Could not resolve imageio_ffmpeg binary: {e}")
    FFMPEG_EXE = "ffmpeg"

# Regex pattern to identify URLs in text
URL_REGEX = re.compile(
    r"(https?://(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|www\.[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|https?://[^\s]+)"
)

# Supported platform domains for friendly UI labeling
PLATFORM_DOMAINS = {
    "instagram.com": "Instagram",
    "instagr.am": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "tiktok.com": "TikTok",
    "pinterest.com": "Pinterest",
    "pin.it": "Pinterest",
    "reddit.com": "Reddit",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
}


def find_urls(text: str) -> List[str]:
    """Extract all URLs from a given message text."""
    if not text:
        return []
    return URL_REGEX.findall(text)


def detect_platform(url: str) -> str:
    """Detect platform name from URL."""
    url_lower = url.lower()
    for domain, name in PLATFORM_DOMAINS.items():
        if domain in url_lower:
            return name
    return "Web Video"


def render_progress_bar(percent: float, width: int = 10) -> str:
    """Generate an ASCII progress bar, e.g. [██████░░░░] 60%."""
    percent = max(0.0, min(100.0, percent))
    filled = int(round(width * percent / 100))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percent:.0f}%"


class DownloaderError(Exception):
    """Custom exception for download errors."""
    pass


class MediaDownloader:
    """High-performance async media downloader using yt-dlp and embedded ffmpeg."""

    def __init__(self, download_dir: Optional[Path] = None):
        if download_dir is None:
            self.download_dir = Path(__file__).resolve().parent / "downloads"
        else:
            self.download_dir = download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def _get_ydl_base_opts(self) -> Dict[str, Any]:
        """Base options for yt-dlp."""
        return {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "trim_file_name": 50,
            "ffmpeg_location": FFMPEG_EXE,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        }

    async def get_media_info(self, url: str) -> Dict[str, Any]:
        """Extract metadata without downloading."""
        opts = self._get_ydl_base_opts()

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await asyncio.to_thread(_extract)
            return info or {}
        except Exception as e:
            logger.error(f"Error extracting media info: {e}")
            raise DownloaderError(f"Could not extract info: {e}")

    async def extract_media_options(self, url: str) -> Dict[str, Any]:
        """
        Extract available resolutions and formats for UI buttons.
        Returns title, uploader, thumbnail, and list of available qualities.
        """
        info = await self.get_media_info(url)
        title = info.get("title", "Video")
        uploader = info.get("uploader") or info.get("channel", "Unknown")
        thumbnail = info.get("thumbnail")
        duration = info.get("duration", 0)

        formats = info.get("formats", [])
        qualities: List[Dict[str, Any]] = []

        # Find available heights
        available_heights = set()
        height_to_filesize = {}

        for f in formats:
            h = f.get("height")
            if h and f.get("vcodec") != "none":
                available_heights.add(h)
                # Estimate filesize
                fs = f.get("filesize") or f.get("filesize_approx")
                if fs and (h not in height_to_filesize or fs > height_to_filesize[h]):
                    height_to_filesize[h] = fs

        # Standard resolution tiers to offer
        tiers = [
            (1080, "1080p FHD"),
            (720, "720p HD"),
            (480, "480p SD"),
            (360, "360p Data Saver"),
        ]

        for height, label in tiers:
            # Check if video has this height or greater
            if any(h >= height for h in available_heights) or not available_heights:
                size_str = ""
                # Approximate size if known
                matching_sizes = [
                    size for h, size in height_to_filesize.items() if h <= height
                ]
                if matching_sizes:
                    mb = max(matching_sizes) / (1024 * 1024)
                    if mb <= 49.0:
                        size_str = f" (~{mb:.1f} MB)"
                    else:
                        continue  # Skip if exceeds 50MB limit

                qualities.append({
                    "height": height,
                    "label": f"{label}{size_str}",
                    "code": f"res_{height}",
                })

        # If no specific heights found (e.g. TikTok, Reels, Twitter), provide standard Best
        if not qualities:
            qualities.append({
                "height": 0,
                "label": "🎬 Best Quality (MP4)",
                "code": "best",
            })

        return {
            "title": title,
            "uploader": uploader,
            "thumbnail": thumbnail,
            "duration": duration,
            "qualities": qualities,
        }

    async def download_video(
        self,
        url: str,
        resolution: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Download video with optional resolution and real-time progress updates.
        """
        temp_dir = Path(tempfile.mkdtemp(dir=str(self.download_dir)))

        ydl_opts = self._get_ydl_base_opts()

        if resolution and resolution > 0:
            format_spec = (
                f"bestvideo[height<={resolution}][filesize<=48M]+bestaudio/"
                f"best[height<={resolution}][filesize<=48M]/"
                f"best[height<={resolution}]/best"
            )
        else:
            format_spec = "bestvideo[filesize<=48M]+bestaudio/best[filesize<=48M]/best[height<=1080]/best"

        ydl_opts.update({
            "paths": {"home": temp_dir.as_posix()},
            "outtmpl": {"default": "%(id).30s.%(ext)s"},
            "format": format_spec,
            "merge_output_format": "mp4",
        })

        if progress_callback:
            def _hook(d):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes") or 0
                    speed = d.get("speed") or 0
                    eta = d.get("eta") or 0
                    percent = (downloaded / total * 100) if total > 0 else 0
                    progress_callback({
                        "percent": percent,
                        "downloaded": downloaded,
                        "total": total,
                        "speed": speed,
                        "eta": eta,
                    })

            ydl_opts["progress_hooks"] = [_hook]

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info

        try:
            info = await asyncio.to_thread(_download)
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.error(f"Download failed: {e}")
            raise DownloaderError(f"Download failed: {e}")

        files = list(temp_dir.glob("*"))
        if not files:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloaderError("No file was saved during download.")

        target_file = files[0]
        for f in files:
            if f.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                target_file = f
                break

        filesize = target_file.stat().st_size
        filesize_mb = filesize / (1024 * 1024)

        if filesize_mb > 50:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloaderError(
                f"Video is too large for Telegram ({filesize_mb:.1f} MB > 50 MB limit)."
            )

        title = info.get("title", "Video")
        uploader = info.get("uploader") or info.get("channel", "Unknown")
        duration = info.get("duration", 0)
        thumbnail = info.get("thumbnail")

        return {
            "type": "video",
            "file_path": target_file,
            "dir_path": temp_dir,
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "thumbnail": thumbnail,
            "filesize_mb": filesize_mb,
        }

    async def download_audio(
        self,
        url: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Download audio as 192k MP3 with embedded metadata & official album art.
        """
        temp_dir = Path(tempfile.mkdtemp(dir=str(self.download_dir)))

        ydl_opts = self._get_ydl_base_opts()
        ydl_opts.update({
            "paths": {"home": temp_dir.as_posix()},
            "outtmpl": {"default": "%(id).30s.%(ext)s"},
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                },
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                },
            ],
        })

        if progress_callback:
            def _hook(d):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes") or 0
                    speed = d.get("speed") or 0
                    eta = d.get("eta") or 0
                    percent = (downloaded / total * 100) if total > 0 else 0
                    progress_callback({
                        "percent": percent,
                        "downloaded": downloaded,
                        "total": total,
                        "speed": speed,
                        "eta": eta,
                    })

            ydl_opts["progress_hooks"] = [_hook]

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info

        try:
            info = await asyncio.to_thread(_download)
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.error(f"Audio download failed: {e}")
            raise DownloaderError(f"Audio download failed: {e}")

        mp3_files = list(temp_dir.glob("*.mp3"))
        if not mp3_files:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloaderError("Audio file was not generated.")

        target_file = mp3_files[0]
        filesize_mb = target_file.stat().st_size / (1024 * 1024)

        if filesize_mb > 50:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloaderError(f"Audio file is too large for Telegram ({filesize_mb:.1f} MB > 50 MB limit).")

        # Process thumbnail for Telegram audio player cover art
        thumb_file = None
        for img_ext in ("*.jpg", "*.jpeg", "*.webp", "*.png"):
            imgs = list(temp_dir.glob(img_ext))
            if imgs:
                raw_thumb = imgs[0]
                # Convert to JPEG if needed
                try:
                    with Image.open(raw_thumb) as img:
                        thumb_path = temp_dir / "thumb.jpg"
                        img.convert("RGB").save(thumb_path, "JPEG", quality=90)
                        thumb_file = thumb_path
                except Exception:
                    thumb_file = raw_thumb
                break

        title = info.get("title", "Audio")
        uploader = info.get("uploader") or info.get("channel", "Unknown")
        duration = info.get("duration", 0)

        return {
            "type": "audio",
            "file_path": target_file,
            "thumb_file": thumb_file,
            "dir_path": temp_dir,
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "filesize_mb": filesize_mb,
        }

    @staticmethod
    def cleanup(dir_path: Path) -> None:
        """Safely clean up temporary files."""
        try:
            if dir_path and dir_path.exists():
                shutil.rmtree(dir_path, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error cleaning up temp directory {dir_path}: {e}")
