import asyncio
import os
import re
import sys
import shutil
import tempfile
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from PIL import Image
import imageio_ffmpeg
import yt_dlp
import requests

import config

try:
    from pinterest_downloader import Pinterest
except ImportError:
    Pinterest = None

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

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


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


def is_pinterest_url(url: str) -> bool:
    """Check if URL belongs to Pinterest."""
    url_lower = url.lower()
    return "pinterest.com" in url_lower or "pin.it" in url_lower


def is_twitter_url(url: str) -> bool:
    """Check if URL belongs to X / Twitter."""
    url_lower = url.lower()
    return "twitter.com" in url_lower or "x.com" in url_lower


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
    """High-performance async media downloader supporting videos, photos, carousels, and audio."""

    def __init__(self, download_dir: Optional[Path] = None):
        if download_dir is None:
            self.download_dir = Path(__file__).resolve().parent / "downloads"
        else:
            self.download_dir = download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def _get_ydl_base_opts(self) -> Dict[str, Any]:
        """Base options for yt-dlp."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "trim_file_name": 50,
            "ffmpeg_location": FFMPEG_EXE,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        }
        if getattr(config, "COOKIES_FILE", None):
            opts["cookiefile"] = str(config.COOKIES_FILE)
        return opts

    def _download_pinterest(self, url: str, temp_dir: Path) -> Dict[str, Any]:
        """Download Pinterest image or video pin using pinterest-downloader."""
        if not Pinterest:
            raise DownloaderError("Pinterest downloader library is not installed.")

        p = Pinterest()
        res = p.download_pin(url, path=str(temp_dir))
        if not res.get("ok"):
            err = res.get("error", {}).get("message") or "Failed to download Pinterest pin"
            raise DownloaderError(f"Pinterest error: {err}")

        media_type = res.get("media_type", "image")
        downloaded_path = Path(res.get("path"))
        if not downloaded_path.exists():
            files = list(temp_dir.glob("*"))
            if not files:
                raise DownloaderError("No Pinterest file saved.")
            downloaded_path = files[0]

        filesize_mb = downloaded_path.stat().st_size / (1024 * 1024)
        if filesize_mb > 50:
            raise DownloaderError(f"File is too large for Telegram ({filesize_mb:.1f} MB > 50 MB limit).")

        out_type = "video" if media_type == "video" or downloaded_path.suffix.lower() in VIDEO_EXTS else "photo"

        return {
            "type": out_type,
            "file_path": downloaded_path,
            "file_paths": [downloaded_path],
            "dir_path": temp_dir,
            "title": "Pinterest Pin",
            "uploader": "Pinterest",
            "duration": 0,
            "thumbnail": None,
            "filesize_mb": filesize_mb,
        }

    def _download_twitter(self, url: str, temp_dir: Path) -> Optional[Dict[str, Any]]:
        """Download Twitter/X post photos or videos via fxTwitter API."""
        m = re.search(r"status/(\d+)", url)
        if not m:
            return None
        tid = m.group(1)

        try:
            resp = requests.get(f"https://api.fxtwitter.com/i/status/{tid}", timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            tw = data.get("tweet", {})
            media = tw.get("media", {})
            photos = media.get("photos") or [item for item in media.get("all", []) if item.get("type") == "photo"]
            videos = media.get("videos") or [item for item in media.get("all", []) if item.get("type") == "video"]
            title = tw.get("text", "X / Twitter Post")
            uploader = tw.get("author", {}).get("name", "X (Twitter)")

            # Photos handling
            if photos and not videos:
                downloaded_files: List[Path] = []
                for idx, p_info in enumerate(photos):
                    p_url = p_info.get("url")
                    if not p_url:
                        continue
                    ext = ".png" if ".png" in p_url.lower() else (".webp" if ".webp" in p_url.lower() else ".jpg")
                    fpath = temp_dir / f"{tid}_{idx}{ext}"
                    r = requests.get(p_url, timeout=20)
                    if r.status_code == 200:
                        with open(fpath, "wb") as f:
                            f.write(r.content)
                        downloaded_files.append(fpath)

                if len(downloaded_files) == 1:
                    mb = downloaded_files[0].stat().st_size / (1024 * 1024)
                    return {
                        "type": "photo",
                        "file_path": downloaded_files[0],
                        "file_paths": downloaded_files,
                        "dir_path": temp_dir,
                        "title": title,
                        "uploader": uploader,
                        "duration": 0,
                        "thumbnail": photos[0].get("url"),
                        "filesize_mb": mb,
                    }
                elif len(downloaded_files) > 1:
                    mb = sum(f.stat().st_size for f in downloaded_files) / (1024 * 1024)
                    return {
                        "type": "album",
                        "file_path": downloaded_files[0],
                        "file_paths": downloaded_files,
                        "dir_path": temp_dir,
                        "title": title,
                        "uploader": uploader,
                        "duration": 0,
                        "thumbnail": photos[0].get("url"),
                        "filesize_mb": mb,
                    }

            # Video handling
            if videos:
                vid_url = videos[0].get("url")
                if vid_url:
                    fpath = temp_dir / f"{tid}.mp4"
                    r = requests.get(vid_url, stream=True, timeout=30)
                    if r.status_code == 200:
                        with open(fpath, "wb") as f:
                            for chunk in r.iter_content(65536):
                                f.write(chunk)
                        mb = fpath.stat().st_size / (1024 * 1024)
                        return {
                            "type": "video",
                            "file_path": fpath,
                            "file_paths": [fpath],
                            "dir_path": temp_dir,
                            "title": title,
                            "uploader": uploader,
                            "duration": int(videos[0].get("duration") or 0),
                            "thumbnail": videos[0].get("thumbnail_url"),
                            "filesize_mb": mb,
                        }
        except Exception as e:
            logger.warning(f"Error in Twitter fx download: {e}")

        return None

    def _download_gallery_dl(self, url: str, temp_dir: Path) -> Optional[Dict[str, Any]]:
        """Fallback extractor for image posts/galleries using gallery-dl."""
        try:
            cmd = [
                sys.executable,
                "-m",
                "gallery_dl",
                "--dest",
                str(temp_dir),
                "--no-mtime",
                url,
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

            all_files = [f for f in temp_dir.rglob("*") if f.is_file()]
            vids = [f for f in all_files if f.suffix.lower() in VIDEO_EXTS]
            imgs = [f for f in all_files if f.suffix.lower() in IMAGE_EXTS]

            if vids:
                target = vids[0]
                mb = target.stat().st_size / (1024 * 1024)
                return {
                    "type": "video",
                    "file_path": target,
                    "file_paths": [target],
                    "dir_path": temp_dir,
                    "title": "Post Video",
                    "uploader": detect_platform(url),
                    "duration": 0,
                    "thumbnail": None,
                    "filesize_mb": mb,
                }
            elif len(imgs) > 1:
                mb = sum(f.stat().st_size for f in imgs) / (1024 * 1024)
                return {
                    "type": "album",
                    "file_path": imgs[0],
                    "file_paths": imgs,
                    "dir_path": temp_dir,
                    "title": "Post Gallery",
                    "uploader": detect_platform(url),
                    "duration": 0,
                    "thumbnail": None,
                    "filesize_mb": mb,
                }
            elif len(imgs) == 1:
                target = imgs[0]
                mb = target.stat().st_size / (1024 * 1024)
                return {
                    "type": "photo",
                    "file_path": target,
                    "file_paths": [target],
                    "dir_path": temp_dir,
                    "title": "Post Photo",
                    "uploader": detect_platform(url),
                    "duration": 0,
                    "thumbnail": None,
                    "filesize_mb": mb,
                }
        except Exception as e:
            logger.warning(f"gallery-dl error: {e}")

        return None

    async def extract_media_options(self, url: str) -> Dict[str, Any]:
        """
        Extract available resolutions and metadata for interactive quality buttons.
        Returns title, uploader, thumbnail, duration, and list of available qualities.
        """
        def _probe():
            opts = self._get_ydl_base_opts()
            opts.update({
                "skip_download": True,
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await asyncio.to_thread(_probe)
        except Exception as e:
            logger.info("Probe info failed, fallback to generic best: %s", e)
            info = {}

        title = (info or {}).get("title") or "Media"
        uploader = (info or {}).get("uploader") or (info or {}).get("channel") or detect_platform(url)
        thumbnail = (info or {}).get("thumbnail")
        duration = (info or {}).get("duration", 0)

        formats = (info or {}).get("formats", [])
        available_heights = set()
        height_to_filesize = {}

        for f in formats:
            h = f.get("height")
            if h and f.get("vcodec") != "none":
                available_heights.add(h)
                fs = f.get("filesize") or f.get("filesize_approx")
                if fs and (h not in height_to_filesize or fs > height_to_filesize[h]):
                    height_to_filesize[h] = fs

        tiers = [
            (1080, "🎬 1080p FHD"),
            (720, "🎬 720p HD"),
            (480, "🎬 480p SD"),
            (360, "🎬 360p Low"),
        ]

        qualities: List[Dict[str, Any]] = []
        for height, label in tiers:
            if any(h >= height for h in available_heights):
                size_str = ""
                matching = [s for h, s in height_to_filesize.items() if h <= height]
                if matching:
                    mb = max(matching) / (1024 * 1024)
                    if mb <= 49.5:
                        size_str = f" (~{mb:.1f}MB)"
                    else:
                        continue  # Exceeds Telegram 50MB limit
                qualities.append({
                    "height": height,
                    "label": f"{label}{size_str}",
                    "code": f"res_{height}",
                })

        if not qualities:
            qualities.append({
                "height": 0,
                "label": "🎬 Best Available",
                "code": "res_best",
            })

        return {
            "title": title,
            "uploader": uploader,
            "thumbnail": thumbnail,
            "duration": duration,
            "qualities": qualities,
        }

    def _download_ytdlp(
        self,
        url: str,
        temp_dir: Path,
        resolution: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Download media using yt-dlp with optional resolution tier."""
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

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        files = [f for f in temp_dir.glob("*") if f.is_file()]
        if not files:
            raise DownloaderError("No file was saved during download.")

        video_files = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
        image_files = [f for f in files if f.suffix.lower() in IMAGE_EXTS]

        if video_files:
            target_file = video_files[0]
            media_type = "video"
            out_files = [target_file]
        elif len(image_files) > 1:
            media_type = "album"
            target_file = image_files[0]
            out_files = image_files
        elif len(image_files) == 1:
            media_type = "photo"
            target_file = image_files[0]
            out_files = [target_file]
        else:
            target_file = files[0]
            media_type = "video"
            out_files = [target_file]

        filesize = sum(f.stat().st_size for f in out_files)
        filesize_mb = filesize / (1024 * 1024)

        if filesize_mb > 50:
            raise DownloaderError(
                f"Media is too large for Telegram ({filesize_mb:.1f} MB > 50 MB limit)."
            )

        title = (info or {}).get("title", "Video")
        uploader = (info or {}).get("uploader") or (info or {}).get("channel", "Unknown")
        duration = (info or {}).get("duration", 0)
        thumbnail = (info or {}).get("thumbnail")

        return {
            "type": media_type,
            "file_path": target_file,
            "file_paths": out_files,
            "dir_path": temp_dir,
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "thumbnail": thumbnail,
            "filesize_mb": filesize_mb,
        }

    async def download_media(
        self,
        url: str,
        resolution: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Main media downloader entrypoint.
        Routes to Pinterest, Twitter, yt-dlp, or gallery-dl.
        """
        temp_dir = Path(tempfile.mkdtemp(dir=str(self.download_dir)))

        try:
            # 1. Specialized Pinterest Downloader
            if is_pinterest_url(url) and Pinterest:
                try:
                    return await asyncio.to_thread(self._download_pinterest, url, temp_dir)
                except Exception as e:
                    logger.warning(f"Pinterest engine error, trying fallback: {e}")

            # 2. Specialized Twitter / X Downloader
            if is_twitter_url(url):
                try:
                    tw_res = await asyncio.to_thread(self._download_twitter, url, temp_dir)
                    if tw_res:
                        return tw_res
                except Exception as e:
                    logger.warning(f"Twitter engine error, trying fallback: {e}")

            # 3. Standard yt-dlp download
            try:
                return await asyncio.to_thread(self._download_ytdlp, url, temp_dir, resolution, progress_callback)
            except Exception as e:
                # 4. Fallback to gallery-dl for posts without video streams
                logger.info(f"Trying gallery-dl fallback for {url} due to: {e}")
                gdl_res = await asyncio.to_thread(self._download_gallery_dl, url, temp_dir)
                if gdl_res:
                    return gdl_res
                raise DownloaderError(f"Download failed: {e}")

        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

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
                return ydl.extract_info(url, download=True)

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
                try:
                    with Image.open(raw_thumb) as img:
                        thumb_path = temp_dir / "thumb.jpg"
                        img.convert("RGB").save(thumb_path, "JPEG", quality=90)
                        thumb_file = thumb_path
                except Exception:
                    thumb_file = raw_thumb
                break

        title = (info or {}).get("title", "Audio")
        uploader = (info or {}).get("uploader") or (info or {}).get("channel", "Unknown")
        duration = (info or {}).get("duration", 0)

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
