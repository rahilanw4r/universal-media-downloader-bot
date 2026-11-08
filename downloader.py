import asyncio
import time
import os
import re
import sys
import html
import json
import secrets
import shutil
import tempfile
import logging
import subprocess
import http.cookiejar
import math
import uuid
from urllib.parse import quote, unquote, urlparse, parse_qs
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = shutil.which("ffmpeg") or "ffmpeg"

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    import requests
except ImportError:
    requests = None

try:
    import instaloader
except ImportError:
    instaloader = None

import config

try:
    from pinterest_downloader import Pinterest
except ImportError:
    Pinterest = None

logger = logging.getLogger(__name__)

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
    "t.co": "X (Twitter)",
    "tiktok.com": "TikTok",
    "pinterest.com": "Pinterest",
    "pin.it": "Pinterest",
    "reddit.com": "Reddit",
    "redd.it": "Reddit",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "fb.com": "Facebook",
    "diskwala.com": "DiskWala",
    "diskwala.fun": "DiskWala",
    "terabox.com": "TeraBox",
    "teraboxapp.com": "TeraBox",
    "terabox.app": "TeraBox",
    "1024tera.com": "TeraBox",
    "1024terabox.com": "TeraBox",
    "terasharelink.com": "TeraBox",
    "teraboxlink.com": "TeraBox",
    "freeterabox.com": "TeraBox",
    "4funbox.com": "TeraBox",
    "mirrobox.com": "TeraBox",
    "nephobox.com": "TeraBox",
    "momerybox.com": "TeraBox",
    "tibibox.com": "TeraBox",
}

DISKWALA_DOMAINS = (
    "diskwala.com",
    "diskwala.fun",
)

TWITTER_DOMAINS = (
    "twitter.com",
    "x.com",
    "t.co",
)

TERABOX_DOMAINS = (
    "terabox.com",
    "teraboxapp.com",
    "terabox.app",
    "1024tera.com",
    "1024terabox.com",
    "terasharelink.com",
    "teraboxlink.com",
    "freeterabox.com",
    "4funbox.com",
    "mirrobox.com",
    "nephobox.com",
    "momerybox.com",
    "tibibox.com",
)

TIKTOK_DOMAINS = (
    "tiktok.com",
    "tiktokv.com",
    "douyin.com",
)

REDDIT_DOMAINS = (
    "reddit.com",
    "redd.it",
    "v.redd.it",
    "i.redd.it",
    "vxreddit.com",
    "rxddit.com",
)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def find_urls(text: str) -> List[str]:
    """Extract all URLs from a given message text."""
    if not text:
        return []
    return URL_REGEX.findall(text)


def _get_url_host(url: str) -> str:
    """Safely extract lowercase hostname from URL without port."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        if ":" in host:
            host = host.split(":")[0]
        return host
    except Exception:
        return ""


def detect_platform(url: str) -> str:
    """Detect platform name from URL using strict host-level matching."""
    host = _get_url_host(url)
    if not host:
        return "Web Video"

    # Specific multi-domain platform groups first (prevents substring conflicts like x.com in terabox.com)
    if any(host == d or host.endswith("." + d) for d in TERABOX_DOMAINS):
        return "TeraBox"
    if any(host == d or host.endswith("." + d) for d in DISKWALA_DOMAINS):
        return "DiskWala"
    if any(host == d or host.endswith("." + d) for d in TWITTER_DOMAINS):
        return "X (Twitter)"
    if any(host == d or host.endswith("." + d) for d in ("instagram.com", "instagr.am")):
        return "Instagram"
    if any(host == d or host.endswith("." + d) for d in ("youtube.com", "youtu.be")):
        return "YouTube"
    if any(host == d or host.endswith("." + d) for d in TIKTOK_DOMAINS):
        return "TikTok"
    if any(host == d or host.endswith("." + d) for d in ("pinterest.com", "pin.it")):
        return "Pinterest"
    if any(host == d or host.endswith("." + d) for d in REDDIT_DOMAINS):
        return "Reddit"
    if any(host == d or host.endswith("." + d) for d in ("facebook.com", "fb.watch", "fb.com")):
        return "Facebook"

    for domain, name in PLATFORM_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return name

    return "Web Video"


def is_pinterest_url(url: str) -> bool:
    """Check if URL belongs to Pinterest using host matching."""
    host = _get_url_host(url)
    return any(host == d or host.endswith("." + d) for d in ("pinterest.com", "pin.it"))


def resolve_pinterest_url(url: str) -> str:
    """Resolve shortened pin.it redirects to the canonical pinterest.com URL."""
    host = _get_url_host(url)
    if host == "pin.it" or host.endswith(".pin.it"):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
        }
        try:
            r = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            if r.url and "pinterest.com" in r.url:
                return r.url
        except Exception:
            pass
    return url


def is_twitter_url(url: str) -> bool:
    """Check if URL belongs to X / Twitter using host matching."""
    host = _get_url_host(url)
    return any(host == d or host.endswith("." + d) for d in TWITTER_DOMAINS)


def is_instagram_url(url: str) -> bool:
    """Check if URL belongs to Instagram using host matching."""
    host = _get_url_host(url)
    return any(host == d or host.endswith("." + d) for d in ("instagram.com", "instagr.am"))


def is_tiktok_url(url: str) -> bool:
    """Check if URL belongs to TikTok."""
    host = _get_url_host(url)
    return any(host == d or host.endswith("." + d) for d in TIKTOK_DOMAINS)


def is_reddit_url(url: str) -> bool:
    """Check if URL belongs to Reddit using host matching."""
    host = _get_url_host(url)
    return any(host == d or host.endswith("." + d) for d in REDDIT_DOMAINS)


def extract_reddit_video_id(url_or_text: str) -> Optional[str]:
    """Extract Reddit video or post ID if present."""
    if not url_or_text:
        return None
    m = re.search(r"v\.redd\.it(?:/|%2F)([a-zA-Z0-9_-]+)", url_or_text)
    if m:
        return m.group(1)
    m = re.search(r"/comments/([a-zA-Z0-9]+)", url_or_text)
    if m:
        return m.group(1)
    m = re.search(r"redd\.it/([a-zA-Z0-9]+)", url_or_text)
    if m:
        return m.group(1)
    return None


def is_terabox_url(url: str) -> bool:
    """Check if URL belongs to TeraBox or any of its mirror domains."""
    host = _get_url_host(url)
    return any(host == d or host.endswith("." + d) for d in TERABOX_DOMAINS)


def is_diskwala_url(url: str) -> bool:
    """Check if URL belongs to DiskWala."""
    host = _get_url_host(url)
    return any(host == d or host.endswith("." + d) for d in DISKWALA_DOMAINS)


def extract_diskwala_id(url: str) -> str:
    """Extract file ID or code from DiskWala URL formats."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            return qs["id"][0].strip()
        path = parsed.path.strip("/")
        for prefix in ("app/", "s/", "c/", "file/"):
            if prefix in path:
                return path.split(prefix)[1].split("/")[0].split("?")[0].strip()
        parts = [p for p in path.split("/") if p]
        if parts:
            return parts[-1].split("?")[0].strip()
    except Exception:
        pass
    return ""


def extract_terabox_surl(url: str) -> str:
    """Extract canonical short URL key from any TeraBox link format."""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "surl" in qs and qs["surl"]:
        surl = qs["surl"][0]
    elif "/s/" in parsed.path:
        surl = parsed.path.split("/s/")[1].split("/")[0].split("?")[0]
    elif "/sharing/link" in parsed.path and "surl" in qs:
        surl = qs["surl"][0]
    else:
        surl = parsed.path.strip("/").split("/")[-1]
    if surl.startswith("1"):
        surl = surl[1:]
    return surl


def render_progress_bar(percent: float, width: int = 10) -> str:
    """Generate an ASCII progress bar, e.g. [██████░░░░] 60%."""
    percent = max(0.0, min(100.0, percent))
    filled = int(round(width * percent / 100))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percent:.0f}%"


class DownloaderError(Exception):
    """Custom exception for download errors."""
    pass


def sanitize_image_file(image_path: Path) -> Optional[Path]:
    """
    Validate that image_path is a real, decodable image and convert it to a standard,
    clean sRGB JPEG. This guarantees Telegram never rejects it with IMAGE_PROCESS_FAILED.
    """
    if not image_path or not image_path.exists() or image_path.stat().st_size < 100:
        return None
    try:
        if not Image:
            return image_path
        with Image.open(image_path) as img:
            w, h = img.size
            if w <= 10 or h <= 10:
                try:
                    image_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return None
            ratio = max(w / h, h / w)
            if ratio > 20:  # Telegram restricts extreme aspect ratios
                try:
                    image_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return None
            if img.mode != "RGB":
                img = img.convert("RGB")
            target_path = image_path.with_suffix(".jpg")
            clean_path = image_path.with_suffix(".clean.jpg")
            img.save(clean_path, "JPEG", quality=92, optimize=True)
            if target_path != image_path and image_path.exists():
                image_path.unlink(missing_ok=True)
            clean_path.replace(target_path)
            return target_path
    except Exception as e:
        logger.warning(f"Could not sanitize image {image_path}: {e}")
        try:
            image_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None


class MediaDownloader:
    """High-performance async media downloader supporting videos, photos, carousels, and audio."""

    def __init__(self, download_dir: Optional[Path] = None):
        if download_dir is None:
            self.download_dir = Path(__file__).resolve().parent / "downloads"
        else:
            self.download_dir = download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def _get_ydl_base_opts(
        self,
        use_cookies: bool = True,
        youtube_clients: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Base options for yt-dlp with anti-bot bypass configuration and fast streaming."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "trim_file_name": 50,
            "ffmpeg_location": FFMPEG_EXE,
            "postprocessor_args": {
                "ffmpeg": ["-movflags", "+faststart"],
            },
        }

        # Check for valid cookies file
        cookie_file = getattr(config, "COOKIES_FILE", None)
        has_cookies = False
        if use_cookies and cookie_file and Path(cookie_file).exists() and Path(cookie_file).stat().st_size > 10:
            opts["cookiefile"] = str(cookie_file)
            has_cookies = True

        opts["extractor_args"] = {
            "instagram": {
                "app_id": ["ios", "web"],
            },
        }
        if youtube_clients:
            opts["extractor_args"]["youtube"] = {
                "player_client": youtube_clients,
            }
        elif not has_cookies:
            # Mobile and web client fallback when running without authentication cookies
            opts["extractor_args"]["youtube"] = {
                "player_client": ["android", "ios", "mweb", "web"],
            }

        return opts

    def _extract_video_thumbnail(self, video_path: Path, temp_dir: Path) -> Optional[Path]:
        """Extract a crisp preview frame from video using FFmpeg for Telegram."""
        thumb_path = temp_dir / f"thumb_{video_path.stem}.jpg"
        try:
            cmd = [
                FFMPEG_EXE, "-y",
                "-ss", "00:00:01",
                "-i", str(video_path),
                "-vframes", "1",
                "-vf", "scale='min(640,iw)':-2",
                "-q:v", "2",
                str(thumb_path),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
            if thumb_path.exists() and thumb_path.stat().st_size > 0:
                return thumb_path
        except Exception:
            pass
        return None

    def _download_pinterest(self, url: str, temp_dir: Path) -> Dict[str, Any]:
        """Download Pinterest image or video pin using direct extraction or pinterest-downloader."""
        if not Pinterest:
            raise DownloaderError("Pinterest downloader library is not installed.")

        url = resolve_pinterest_url(url)
        p = Pinterest()

        # 1. Try to inspect pin metadata first to extract direct 720p MP4 or high-res photo
        try:
            pin_res = p.get_pin(url)
            if pin_res.get("ok"):
                pin_data = pin_res.get("pin", {})
                title = pin_data.get("title") or pin_data.get("description") or "Pinterest Pin"
                author = pin_res.get("author", {}).get("username") or "Pinterest"

                # Check for direct MP4 video stream
                if pin_data.get("media_type") == "video":
                    formats = (pin_data.get("video") or {}).get("formats") or []
                    mp4_formats = [f for f in formats if ".mp4" in f.get("url", "").lower()]
                    if mp4_formats:
                        p720 = [f for f in mp4_formats if "720p" in f.get("url", "").lower() or f.get("quality") == "V_EXP7"]
                        best_vid = p720[0] if p720 else mp4_formats[0]
                        vid_url = best_vid.get("url")
                        if vid_url:
                            vid_path = temp_dir / f"{pin_data.get('id', 'pin')}.mp4"
                            headers = {
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                                )
                            }
                            resp = requests.get(vid_url, headers=headers, stream=True, timeout=30)
                            if resp.status_code == 200:
                                with open(vid_path, "wb") as f:
                                    for chunk in resp.iter_content(chunk_size=65536):
                                        if chunk:
                                            f.write(chunk)
                                mb = vid_path.stat().st_size / (1024 * 1024)
                                return {
                                    "type": "video",
                                    "file_path": vid_path,
                                    "file_paths": [vid_path],
                                    "dir_path": temp_dir,
                                    "title": title,
                                    "uploader": author,
                                    "duration": (pin_data.get("video") or {}).get("duration", 0) / 1000,
                                    "thumbnail": (pin_data.get("video") or {}).get("poster"),
                                    "filesize_mb": mb,
                                }
        except Exception as e:
            logger.debug("Direct Pinterest inspection error: %s", e)

        # 2. Download via download_pin
        res = p.download_pin(url, path=str(temp_dir))
        if not res.get("ok"):
            err = res.get("error", {}).get("message") or "Failed to download Pinterest pin"
            raise DownloaderError(f"Pinterest error: {err}")

        downloaded_path = Path(res.get("path"))
        if not downloaded_path.exists():
            files = list(temp_dir.glob("*"))
            if not files:
                raise DownloaderError("No Pinterest file saved.")
            downloaded_path = files[0]

        filesize_mb = downloaded_path.stat().st_size / (1024 * 1024)
        if filesize_mb > config.MAX_FILE_SIZE_MB:
            raise DownloaderError(f"File is too large for Telegram ({filesize_mb:.1f} MB > {config.MAX_FILE_SIZE_MB:.0f} MB limit).")

        # Strictly determine output type by actual saved file extension
        out_type = "video" if downloaded_path.suffix.lower() in VIDEO_EXTS else "photo"

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
        """Download Twitter/X post photos or videos via fxTwitter / vxTwitter API."""
        if not requests:
            return None

        m = re.search(r"status/(\d+)", url)
        if not m:
            return None
        tid = m.group(1)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

        # Multiple resilient API endpoints
        endpoints = [
            f"https://api.fxtwitter.com/i/status/{tid}",
            f"https://api.vxtwitter.com/Twitter/status/{tid}",
            f"https://api.fixupx.com/i/status/{tid}",
        ]

        data = None
        for endpoint in endpoints:
            try:
                resp = requests.get(endpoint, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        break
            except Exception:
                continue

        if not data:
            return None

        try:
            tw = data.get("tweet") or data
            media = tw.get("media") or {}
            title = tw.get("text") or data.get("text") or "X / Twitter Post"
            uploader = (
                (tw.get("author") or {}).get("name")
                or data.get("user_name")
                or (tw.get("author") or {}).get("screen_name")
                or "X (Twitter)"
            )

            photos: List[Dict[str, Any]] = []
            videos: List[Dict[str, Any]] = []

            # 1. Parse from standard fxtwitter media structure
            if isinstance(media, dict):
                photos = media.get("photos") or [item for item in media.get("all", []) if item.get("type") == "photo"]
                videos = media.get("videos") or [item for item in media.get("all", []) if item.get("type") == "video"]

            # 2. Parse from vxtwitter media_extended or mediaURLs fallback
            if not photos and not videos:
                extended = data.get("media_extended") or tw.get("media_extended") or []
                for item in extended:
                    itype = item.get("type")
                    if itype in ("image", "photo"):
                        photos.append({"url": item.get("url")})
                    elif itype == "video":
                        videos.append({"url": item.get("url"), "duration": item.get("duration", 0)})

                if not photos and not videos:
                    urls = data.get("mediaURLs") or tw.get("mediaURLs") or []
                    for u in urls:
                        if ".mp4" in u.lower():
                            videos.append({"url": u})
                        else:
                            photos.append({"url": u})

            # Video handling
            if videos:
                vid_url = videos[0].get("url")
                if vid_url:
                    fpath = temp_dir / f"{tid}.mp4"
                    r = requests.get(vid_url, headers=headers, stream=True, timeout=30)
                    if r.status_code == 200:
                        with open(fpath, "wb") as f:
                            for chunk in r.iter_content(65536):
                                f.write(chunk)
                        mb = fpath.stat().st_size / (1024 * 1024)
                        thumb_file = self._extract_video_thumbnail(fpath, temp_dir)
                        return {
                            "type": "video",
                            "file_path": fpath,
                            "file_paths": [fpath],
                            "dir_path": temp_dir,
                            "title": title,
                            "uploader": uploader,
                            "duration": int(videos[0].get("duration") or 0),
                            "thumbnail": videos[0].get("thumbnail_url"),
                            "thumb_file": thumb_file,
                            "filesize_mb": mb,
                        }

            # Photos handling
            if photos:
                downloaded_files: List[Path] = []
                for idx, p_info in enumerate(photos):
                    p_url = p_info.get("url")
                    if not p_url:
                        continue
                    # Upgrade to highest quality on twimg
                    if "twimg.com" in p_url and "name=" not in p_url:
                        sep = "&" if "?" in p_url else "?"
                        p_url = f"{p_url}{sep}name=orig"

                    ext = ".png" if ".png" in p_url.lower() else (".webp" if ".webp" in p_url.lower() else ".jpg")
                    fpath = temp_dir / f"{tid}_{idx}{ext}"
                    r = requests.get(p_url, headers=headers, timeout=20)
                    if r.status_code == 200 and r.content:
                        with open(fpath, "wb") as f:
                            f.write(r.content)
                        valid_img = sanitize_image_file(fpath)
                        if valid_img:
                            downloaded_files.append(valid_img)

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
        except Exception as e:
            logger.warning("Error in Twitter extraction: %s", e)

        return None

    def _download_terabox(
        self,
        url: str,
        temp_dir: Path,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Download files/videos from TeraBox and all supported mirror domains:
        1. Canonicalize HTTP redirects to extract direct shorturl/surl.
        2. Unified resolver gateways (Cloudflare Worker proxies, diskwala.fun/api/resolve).
        3. Direct TeraBox share/list API with optional ndus cookie.
        4. Fallback public resolver APIs and direct HTML scraping.
        Supports chunked resume-enabled streaming and live progress reporting.
        """
        if not requests:
            raise DownloaderError("Requests library not available.")

        # Follow fast redirects to unmask canonical sharing link if necessary
        try:
            head_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
            }
            test_url = url
            if "terabox.com" in test_url and "terabox.app" not in test_url:
                test_url = test_url.replace("terabox.com", "www.terabox.app")
            head_resp = requests.head(test_url, headers=head_headers, allow_redirects=True, timeout=4)
            if head_resp.url and head_resp.url != url:
                url = head_resp.url
        except Exception:
            pass

        surl = extract_terabox_surl(url)
        if not surl:
            raise DownloaderError("Could not extract TeraBox file ID from link.")

        # Prepare both surl with '1' and clean surl for maximum resolver compatibility
        surls_to_try = [surl]
        if surl.startswith("1") and len(surl) > 1:
            surls_to_try.append(surl[1:])
        elif not surl.startswith("1"):
            surls_to_try.append("1" + surl)

        # Check for user-provided TeraBox cookie (ndus)
        raw_cookie = os.getenv("TERABOX_COOKIE", "").strip() or os.getenv("NDUS", "").strip()
        ndus_cookie = ""
        if raw_cookie:
            if "ndus=" in raw_cookie:
                ndus_cookie = raw_cookie.split("ndus=")[1].split(";")[0].strip()
            else:
                ndus_cookie = raw_cookie
        elif getattr(config, "COOKIES_FILE", None) and Path(config.COOKIES_FILE).exists():
            try:
                with open(config.COOKIES_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "ndus" in line:
                            if "\t" in line:
                                parts = line.strip().split("\t")
                                if len(parts) >= 7 and "ndus" in parts[5]:
                                    ndus_cookie = parts[6].strip()
                                    break
                            elif "ndus=" in line:
                                ndus_cookie = line.split("ndus=")[1].split(";")[0].split()[0].strip()
                                break
            except Exception:
                pass

        logger.info(f"Extracting TeraBox link for surl={surl} (has_ndus={bool(ndus_cookie)})...")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.terabox.app/",
        }
        if ndus_cookie:
            headers["Cookie"] = f"ndus={ndus_cookie}"

        file_info = None

        # Tier 1: Direct Webpage HTML Token Extraction & API (www.terabox.app & www.1024tera.com)
        for s in surls_to_try:
            for web_host in ("www.terabox.app", "www.1024tera.com"):
                try:
                    web_url = f"https://{web_host}/sharing/link?surl={s}"
                    s_resp = requests.get(
                        web_url,
                        headers={
                            "User-Agent": headers["User-Agent"],
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Cookie": headers.get("Cookie", ""),
                        },
                        timeout=8,
                        allow_redirects=True,
                    )
                    if s_resp.status_code == 200:
                        html_text = s_resp.text
                        js_token = None
                        if 'fn%28%22' in html_text and '%22%29' in html_text:
                            js_token = html_text.split('fn%28%22')[1].split('%22%29')[0]
                        elif 'window.jsToken' in html_text:
                            js_m = re.search(r'jsToken\s*=\s*["\']([^"\']+)["\']', html_text)
                            if js_m:
                                js_token = js_m.group(1)

                        if js_token:
                            api_headers = dict(headers)
                            api_headers["Referer"] = web_url
                            params = {
                                "app_id": "250528",
                                "web": "1",
                                "channel": "dubox",
                                "clienttype": "0",
                                "jsToken": js_token,
                                "page": "1",
                                "num": "20",
                                "by": "name",
                                "order": "asc",
                                "shorturl": s,
                                "root": "1",
                            }
                            r2 = requests.get(f"https://{web_host}/share/list", headers=api_headers, params=params, timeout=10)
                            if r2.status_code == 200:
                                data = r2.json()
                                items = data.get("list") or []
                                if items and isinstance(items, list):
                                    first = items[0]
                                    if first.get("dlink"):
                                        file_info = {
                                            "file_name": first.get("server_filename") or f"terabox_{s}.mp4",
                                            "dlink": first.get("dlink"),
                                            "size": int(first.get("size", 0)),
                                            "thumb": (first.get("thumbs") or {}).get("url3"),
                                        }
                                        break
                except Exception as e:
                    logger.debug("Direct TeraBox web resolution failed on %s for %s: %s", web_host, s, e)
            if file_info:
                break

        # Tier 2: Cloudflare Worker Unified Proxies (mode=resolve)
        if not file_info:
            proxy_endpoints = []
            for s in surls_to_try:
                proxy_endpoints.append(f"https://tbx-proxy.shakir-ansarii075.workers.dev/?mode=resolve&surl={s}")
                proxy_endpoints.append(f"https://terabox-dl.qtcloud.workers.dev/api/get-info?shorturl={s}")

            for p_url in proxy_endpoints:
                try:
                    r = requests.get(p_url, headers=headers, timeout=10)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, list) and data:
                            first = data[0]
                            d = first.get("dlink") or first.get("download_link") or first.get("download_url")
                            if d:
                                file_info = {
                                    "file_name": first.get("server_filename") or first.get("file_name") or "terabox_media.mp4",
                                    "dlink": d,
                                    "size": int(first.get("size") or first.get("size_bytes") or 0),
                                    "thumb": (first.get("thumbs") or {}).get("url3") or first.get("thumbnail"),
                                }
                                break
                        elif isinstance(data, dict):
                            first = (data.get("list") or [None])[0] or data.get("data") or data
                            if isinstance(first, dict):
                                d = first.get("dlink") or first.get("download_link") or first.get("download_url")
                                if d:
                                    file_info = {
                                        "file_name": first.get("server_filename") or first.get("file_name") or "terabox_media.mp4",
                                        "dlink": d,
                                        "size": int(first.get("size") or first.get("size_bytes") or 0),
                                        "thumb": (first.get("thumbs") or {}).get("url3") or first.get("thumbnail"),
                                    }
                                    break
                except Exception as e:
                    logger.debug("Proxy resolution failed for %s: %s", p_url, e)

        # Tier 3: Public Multi-Platform Resolver (diskwala.fun/api/resolve)
        if not file_info:
            try:
                fun_resp = requests.post(
                    "https://diskwala.fun/api/resolve",
                    json={"url": url},
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": headers["User-Agent"],
                        "Referer": "https://diskwala.fun/",
                        "Origin": "https://diskwala.fun",
                        "Accept": "application/json",
                    },
                    timeout=12,
                )
                if fun_resp.status_code == 200:
                    data = fun_resp.json()
                    res = data.get("result") or data.get("data") or {}
                    d = res.get("downloadUrl") or res.get("watchUrl") or res.get("url") or res.get("dlink")
                    if d:
                        file_info = {
                            "file_name": res.get("title") or res.get("fileName") or f"terabox_{surl}.mp4",
                            "dlink": d,
                            "size": int(res.get("size") or res.get("fileSize") or 0),
                            "thumb": res.get("thumbnail") or res.get("thumb"),
                        }
            except Exception as e:
                logger.debug("Diskwala resolver tier failed for TeraBox: %s", e)

        # Tier 4: Direct TeraBox API with app_id=250528
        if not file_info:
            api_hosts = ["www.terabox.app", "www.1024tera.com"]
            for host in api_hosts:
                for s in surls_to_try:
                    try:
                        api_url = f"https://{host}/share/list?app_id=250528&shorturl={s}&root=1"
                        r = requests.get(api_url, headers=headers, timeout=10)
                        if r.status_code == 200:
                            data = r.json()
                            items = data.get("list") or []
                            if items and isinstance(items, list):
                                first = items[0]
                                if first.get("dlink"):
                                    file_info = {
                                        "file_name": first.get("server_filename") or f"terabox_{s}.mp4",
                                        "dlink": first.get("dlink"),
                                        "size": int(first.get("size", 0)),
                                        "thumb": (first.get("thumbs") or {}).get("url3"),
                                    }
                                    break
                    except Exception as e:
                        logger.debug("Direct TeraBox API failed on %s for %s: %s", host, s, e)
                if file_info:
                    break

        if not file_info or not file_info.get("dlink"):
            if not ndus_cookie:
                raise DownloaderError(
                    "TeraBox links require an account session cookie (NDUS). "
                    "TeraBox restricts unauthenticated access with verification challenges.\n\n"
                    "💡 How to configure TeraBox:\n"
                    "1. Log in to terabox.com in your browser.\n"
                    "2. Open Developer Tools (F12) -> Application -> Cookies -> Copy 'ndus'.\n"
                    "3. Set TERABOX_COOKIE=<your_ndus> in .env or send your cookies.txt with /cookies."
                )
            else:
                raise DownloaderError(
                    "Unable to resolve TeraBox download link. "
                    "The provided session cookie may be expired, or the file is private/removed. "
                    "Please update your cookie using /cookies."
                )

        file_name = file_info["file_name"]
        dlink = file_info["dlink"]
        expected_size = file_info.get("size", 0)

        # Sanitize filename
        safe_name = re.sub(r'[\\/*?:"<>|]', "", file_name).strip() or f"terabox_{surl}.mp4"
        out_path = temp_dir / safe_name

        # Download stream with progress hooks
        dl_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": "https://www.terabox.app/",
            "Connection": "keep-alive",
        }
        if ndus_cookie:
            dl_headers["Cookie"] = f"ndus={ndus_cookie}"

        logger.info(f"Downloading TeraBox file '{safe_name}'...")
        with requests.get(dlink, headers=dl_headers, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total_bytes = int(resp.headers.get("content-length", 0)) or expected_size
            downloaded = 0
            start_t = time.time()
            last_t = start_t
            last_bytes = 0

            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        cur_t = time.time()
                        if progress_callback and (cur_t - last_t >= 1.5 or (total_bytes and downloaded >= total_bytes)):
                            elapsed = cur_t - last_t
                            speed = (downloaded - last_bytes) / elapsed if elapsed > 0 else 0
                            pct = (downloaded / total_bytes * 100) if total_bytes > 0 else 0
                            rem = (total_bytes - downloaded) if total_bytes > 0 else 0
                            eta = rem / speed if speed > 0 else 0
                            last_t = cur_t
                            last_bytes = downloaded
                            progress_callback({
                                "percent": pct,
                                "downloaded": downloaded,
                                "total": total_bytes,
                                "speed": speed,
                                "eta": eta,
                            })

        filesize_mb = out_path.stat().st_size / (1024 * 1024)
        is_vid = out_path.suffix.lower() in VIDEO_EXTS
        is_img = out_path.suffix.lower() in IMAGE_EXTS
        is_comp = False

        # Attempt optional compression or zero-loss splitting if video exceeds configured limit
        is_split = False
        parts = [out_path]
        if filesize_mb > config.MAX_FILE_SIZE_MB and is_vid and config.ENABLE_VIDEO_COMPRESSION:
            logger.info("TeraBox video exceeds %.0fMB (%.1f MB), attempting fast compression...", config.MAX_FILE_SIZE_MB, filesize_mb)
            compressed = self._compress_video_to_fit(out_path, temp_dir, duration=0)
            if compressed and compressed.exists():
                out_path = compressed
                filesize_mb = out_path.stat().st_size / (1024 * 1024)
                parts = [out_path]
                is_comp = True

        if not is_comp and filesize_mb > config.MAX_FILE_SIZE_MB and is_vid and config.AUTO_SPLIT_OVERSIZED_VIDEOS:
            split_parts = self._split_video_into_parts(out_path, temp_dir)
            if len(split_parts) > 1:
                parts = split_parts
                out_path = split_parts[0]
                is_split = True
                logger.info("Auto-split oversized TeraBox video into %d parts", len(parts))

        if not is_split and not is_comp and filesize_mb > config.MAX_FILE_SIZE_MB:
            raise DownloaderError(
                f"File is too large for Telegram bot delivery ({filesize_mb:.1f} MB exceeds {config.MAX_FILE_SIZE_MB:.0f} MB limit)."
            )

        # Extract crisp thumbnail for video
        thumb_file = None
        duration = 0
        if is_vid:
            thumb_file = self._extract_video_thumbnail(out_path, temp_dir)
            try:
                cmd = [FFMPEG_EXE, "-i", str(out_path)]
                p = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, timeout=10)
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p.stderr)
                if m:
                    h, mn, s = m.groups()
                    duration = int(h) * 3600 + int(mn) * 60 + float(s)
            except Exception:
                pass

        out_type = "video" if is_vid else ("photo" if is_img else "video")

        return {
            "type": out_type,
            "file_path": out_path,
            "file_paths": parts,
            "dir_path": temp_dir,
            "title": safe_name,
            "uploader": "TeraBox",
            "duration": duration,
            "thumbnail": file_info.get("thumb"),
            "thumb_file": thumb_file,
            "is_compressed": is_comp,
            "is_split": is_split,
            "filesize_mb": filesize_mb,
        }

    def _download_diskwala(
        self,
        url: str,
        temp_dir: Path,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Download files/videos from DiskWala (diskwala.com, diskwala.fun):
        1. Custom API endpoint if configured (DISKWALA_API_URL / DISKWALA_API_KEY).
        2. Direct resolution gateway (https://diskwala.fun/api/resolve).
        3. Web metadata & direct CDN scraping.
        Supports chunked resume-enabled streaming and live progress reporting.
        """
        if not requests:
            raise DownloaderError("Requests library not available.")

        file_id = extract_diskwala_id(url)
        logger.info(f"Extracting DiskWala file for id='{file_id}' (url={url})...")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://diskwala.com/",
            "Origin": "https://diskwala.com",
        }

        file_info = None

        # Tier 1: Configured Custom API (e.g. diskwaladevapi or private resolver)
        api_url = getattr(config, "DISKWALA_API_URL", "").strip()
        api_key = getattr(config, "DISKWALA_API_KEY", "").strip()
        if api_url:
            try:
                custom_headers = dict(headers)
                if api_key:
                    custom_headers["x-api-key"] = api_key
                    custom_headers["Authorization"] = f"Bearer {api_key}"
                r = requests.post(api_url, json={"url": url, "id": file_id}, headers=custom_headers, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    dl = data.get("download_url") or data.get("downloadUrl") or data.get("url") or data.get("dlink")
                    if dl:
                        file_info = {
                            "file_name": data.get("file_name") or data.get("title") or f"diskwala_{file_id}.mp4",
                            "dlink": dl,
                            "size": int(data.get("size") or data.get("file_size") or 0),
                            "thumb": data.get("thumbnail") or data.get("thumb"),
                        }
            except Exception as e:
                logger.debug(f"Custom DiskWala API failed: {e}")

        # Tier 2: Public Resolver Gateways (thediskwala.com & playdiskwala.in)
        if not file_info:
            import urllib.parse
            encoded_url = urllib.parse.quote(url, safe="")
            free_endpoints = [
                f"https://thediskwala.com/api/diskwala-free?url={encoded_url}",
                f"https://playdiskwala.in/api/diskwala-free?url={encoded_url}",
            ]
            for ep in free_endpoints:
                try:
                    ref_host = "https://thediskwala.com/" if "thediskwala" in ep else "https://playdiskwala.in/"
                    r = requests.get(
                        ep,
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                            ),
                            "Referer": ref_host,
                            "Accept": "application/json, text/plain, */*",
                        },
                        timeout=12,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        dl = (
                            data.get("downloadUrl")
                            or data.get("download_url")
                            or data.get("videoUrl")
                            or data.get("video_url")
                            or data.get("streamUrl")
                            or data.get("url")
                            or data.get("dlink")
                        )
                        if dl:
                            file_info = {
                                "file_name": data.get("title") or data.get("file_name") or f"diskwala_{file_id}.mp4",
                                "dlink": dl,
                                "size": int(data.get("size") or data.get("file_size") or 0),
                                "thumb": data.get("thumbnail") or data.get("thumb"),
                            }
                            break
                except Exception as e:
                    logger.debug(f"Free DiskWala resolver {ep} failed: {e}")

        # Tier 3: Upstream Multi-Platform Resolver (diskwala.fun/api/resolve)
        if not file_info:
            try:
                fun_resp = requests.post(
                    "https://diskwala.fun/api/resolve",
                    json={"url": url},
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://diskwala.fun/",
                        "Origin": "https://diskwala.fun",
                        "Accept": "application/json",
                    },
                    timeout=15,
                )
                if fun_resp.status_code == 200:
                    data = fun_resp.json()
                    res = data.get("result") or data.get("data") or {}
                    dlink = res.get("downloadUrl") or res.get("watchUrl") or res.get("url") or res.get("dlink")
                    if dlink:
                        file_info = {
                            "file_name": res.get("title") or res.get("fileName") or f"diskwala_{file_id}.mp4",
                            "dlink": dlink,
                            "size": int(res.get("size") or res.get("fileSize") or 0),
                            "thumb": res.get("thumbnail") or res.get("thumb"),
                        }
            except Exception as e:
                logger.debug(f"DiskWala fun resolver failed: {e}")

        # Tier 4: Direct Webpage HTML Token / og:video Scraping
        if not file_info:
            try:
                candidate_urls = [url]
                if file_id:
                    candidate_urls.extend([
                        f"https://diskwala.com/app/{file_id}",
                        f"https://diskwala.fun/s/{file_id}",
                        f"https://www.diskwala.com/s/{file_id}",
                    ])
                for c_url in candidate_urls:
                    try:
                        r = requests.get(c_url, headers=headers, timeout=12, allow_redirects=True)
                        if r.status_code == 200:
                            html_text = r.text
                            v_match = re.search(r'property="og:video(?::secure_url)?"\s+content="([^"]+)"', html_text, re.I)
                            if not v_match:
                                v_match = re.search(r'content="([^"]+)"\s+property="og:video(?::secure_url)?"', html_text, re.I)
                            if not v_match:
                                v_match = re.search(r'(?:source\s+src|video\s+src)="([^"]+)"', html_text, re.I)

                            title_m = re.search(r'property="og:title"\s+content="([^"]+)"', html_text, re.I)
                            title = title_m.group(1).strip() if title_m else f"diskwala_{file_id}.mp4"

                            if v_match:
                                file_info = {
                                    "file_name": title,
                                    "dlink": v_match.group(1),
                                    "size": 0,
                                    "thumb": None,
                                }
                                break
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Direct HTML scraping for DiskWala failed: {e}")

        if not file_info or not file_info.get("dlink"):
            raise DownloaderError(
                "Unable to resolve DiskWala link. DiskWala links are protected by AppiCrypt DRM verification. "
                "To enable automated DiskWala downloads, configure a private resolver in .env (DISKWALA_API_URL and DISKWALA_API_KEY) "
                "or upload an active cookies.txt via /cookies."
            )

        file_name = file_info["file_name"]
        dlink = file_info["dlink"]
        expected_size = file_info.get("size", 0)

        # Sanitize filename
        safe_name = re.sub(r'[\\/*?:"<>|]', "", file_name).strip() or f"diskwala_{file_id}.mp4"
        if not any(safe_name.lower().endswith(ext) for ext in VIDEO_EXTS | IMAGE_EXTS):
            safe_name += ".mp4"
        out_path = temp_dir / safe_name

        dl_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": "https://diskwala.com/",
            "Connection": "keep-alive",
        }

        logger.info(f"Downloading DiskWala file '{safe_name}'...")
        with requests.get(dlink, headers=dl_headers, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total_bytes = int(resp.headers.get("content-length", 0)) or expected_size
            downloaded = 0
            start_t = time.time()
            last_t = start_t
            last_bytes = 0

            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        cur_t = time.time()
                        if progress_callback and (cur_t - last_t >= 1.5 or (total_bytes and downloaded >= total_bytes)):
                            elapsed = cur_t - last_t
                            speed = (downloaded - last_bytes) / elapsed if elapsed > 0 else 0
                            pct = (downloaded / total_bytes * 100) if total_bytes > 0 else 0
                            rem = (total_bytes - downloaded) if total_bytes > 0 else 0
                            eta = rem / speed if speed > 0 else 0
                            last_t = cur_t
                            last_bytes = downloaded
                            progress_callback({
                                "percent": pct,
                                "downloaded": downloaded,
                                "total": total_bytes,
                                "speed": speed,
                                "eta": eta,
                            })

        filesize_mb = out_path.stat().st_size / (1024 * 1024)
        is_vid = out_path.suffix.lower() in VIDEO_EXTS
        is_img = out_path.suffix.lower() in IMAGE_EXTS
        is_comp = False

        # Attempt optional compression or zero-loss splitting if video exceeds configured limit
        is_split = False
        parts = [out_path]
        if filesize_mb > config.MAX_FILE_SIZE_MB and is_vid and config.ENABLE_VIDEO_COMPRESSION:
            logger.info("DiskWala video exceeds %.0fMB (%.1f MB), attempting fast compression...", config.MAX_FILE_SIZE_MB, filesize_mb)
            compressed = self._compress_video_to_fit(out_path, temp_dir, duration=0)
            if compressed and compressed.exists():
                out_path = compressed
                filesize_mb = out_path.stat().st_size / (1024 * 1024)
                parts = [out_path]
                is_comp = True

        if not is_comp and filesize_mb > config.MAX_FILE_SIZE_MB and is_vid and config.AUTO_SPLIT_OVERSIZED_VIDEOS:
            split_parts = self._split_video_into_parts(out_path, temp_dir)
            if len(split_parts) > 1:
                parts = split_parts
                out_path = split_parts[0]
                is_split = True
                logger.info("Auto-split oversized DiskWala video into %d parts", len(parts))

        if not is_split and not is_comp and filesize_mb > config.MAX_FILE_SIZE_MB:
            raise DownloaderError(
                f"File is too large for Telegram bot delivery ({filesize_mb:.1f} MB exceeds {config.MAX_FILE_SIZE_MB:.0f} MB limit)."
            )

        # Extract crisp thumbnail for video
        thumb_file = None
        duration = 0
        if is_vid:
            thumb_file = self._extract_video_thumbnail(out_path, temp_dir)
            try:
                cmd = [FFMPEG_EXE, "-i", str(out_path)]
                p = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, timeout=10)
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p.stderr)
                if m:
                    h, mn, s = m.groups()
                    duration = int(h) * 3600 + int(mn) * 60 + float(s)
            except Exception:
                pass

        out_type = "video" if is_vid else ("photo" if is_img else "video")

        return {
            "type": out_type,
            "file_path": out_path,
            "file_paths": parts,
            "dir_path": temp_dir,
            "title": safe_name,
            "uploader": "DiskWala",
            "duration": duration,
            "thumbnail": file_info.get("thumb"),
            "thumb_file": thumb_file,
            "is_compressed": is_comp,
            "is_split": is_split,
            "filesize_mb": filesize_mb,
        }

    def _download_tiktok(
        self,
        url: str,
        temp_dir: Path,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Download TikTok media using high-speed watermark-free direct API (TikWM)
        with regional ISP bypass (essential for regions where tiktok.com is blocked).
        Supports:
        - Clean watermark-free HD videos
        - Photo slide/carousel albums
        - Video title, creator, cover art, and duration
        """
        if not requests:
            return None

        logger.info(f"Extracting TikTok media for {url} via direct bypass API...")
        api_url = "https://www.tikwm.com/api/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

        try:
            resp = requests.post(
                api_url,
                data={"url": url, "hd": 1},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"TikWM API status {resp.status_code}")
                return None

            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                logger.warning(f"TikWM API error: {data.get('msg')}")
                return None

            tt_data = data["data"]
            vid_id = str(tt_data.get("id") or int(time.time()))
            title = tt_data.get("title") or "TikTok Media"
            author = (tt_data.get("author") or {}).get("nickname") or (tt_data.get("author") or {}).get("unique_id") or "TikTok"
            duration = int(tt_data.get("duration") or 0)
            cover_url = tt_data.get("cover")

            # 1. Download Cover Art if available
            thumb_file = None
            if cover_url:
                try:
                    c_resp = requests.get(cover_url, headers=headers, timeout=10)
                    if c_resp.status_code == 200:
                        t_path = temp_dir / f"tiktok_thumb_{vid_id}.jpg"
                        with open(t_path, "wb") as f:
                            f.write(c_resp.content)
                        thumb_file = t_path
                except Exception:
                    thumb_file = None

            # 2. Check for Photo Carousel / Slide Images
            images = tt_data.get("images") or []
            if images:
                img_paths = []
                for idx, img_url in enumerate(images):
                    try:
                        i_resp = requests.get(img_url, headers=headers, timeout=15)
                        if i_resp.status_code == 200:
                            i_path = temp_dir / f"tiktok_{vid_id}_{idx+1}.jpg"
                            with open(i_path, "wb") as f:
                                f.write(i_resp.content)
                            img_paths.append(i_path)
                    except Exception as e:
                        logger.debug(f"Failed to download TikTok slide image {idx}: {e}")

                if img_paths:
                    return {
                        "type": "album",
                        "file_paths": img_paths,
                        "file_path": img_paths[0],
                        "dir_path": temp_dir,
                        "title": title,
                        "uploader": author,
                        "duration": 0,
                        "thumbnail": cover_url,
                        "thumb_file": thumb_file,
                        "filesize_mb": sum(p.stat().st_size for p in img_paths) / (1024 * 1024),
                    }

            # 3. Video Download (HD preferred, fallback to standard non-watermarked)
            video_url = tt_data.get("hdplay") or tt_data.get("play")
            if not video_url:
                return None

            video_path = temp_dir / f"tiktok_{vid_id}.mp4"
            with requests.get(video_url, headers=headers, stream=True, timeout=30) as v_resp:
                v_resp.raise_for_status()
                total_bytes = int(v_resp.headers.get("content-length", 0)) or int(tt_data.get("hd_size") or tt_data.get("size") or 0)
                downloaded = 0
                start_t = time.time()
                last_t = start_t
                last_bytes = 0

                with open(video_path, "wb") as f:
                    for chunk in v_resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            cur_t = time.time()
                            if progress_callback and (cur_t - last_t >= 1.5 or (total_bytes and downloaded >= total_bytes)):
                                elapsed = cur_t - last_t
                                speed = (downloaded - last_bytes) / elapsed if elapsed > 0 else 0
                                pct = (downloaded / total_bytes * 100) if total_bytes > 0 else 0
                                rem = (total_bytes - downloaded) if total_bytes > 0 else 0
                                eta = rem / speed if speed > 0 else 0
                                last_t = cur_t
                                last_bytes = downloaded
                                progress_callback({
                                    "percent": pct,
                                    "downloaded": downloaded,
                                    "total": total_bytes,
                                    "speed": speed,
                                    "eta": eta,
                                })

            filesize_mb = video_path.stat().st_size / (1024 * 1024)
            is_split = False
            parts = [video_path]

            if not thumb_file:
                thumb_file = self._extract_video_thumbnail(video_path, temp_dir)

            if filesize_mb > config.MAX_FILE_SIZE_MB and config.AUTO_SPLIT_OVERSIZED_VIDEOS:
                split_parts = self._split_video_into_parts(video_path, temp_dir)
                if len(split_parts) > 1:
                    parts = split_parts
                    video_path = split_parts[0]
                    is_split = True

            return {
                "type": "video",
                "file_path": video_path,
                "file_paths": parts,
                "dir_path": temp_dir,
                "title": title,
                "uploader": author,
                "duration": duration,
                "thumbnail": cover_url,
                "thumb_file": thumb_file,
                "filesize_mb": filesize_mb,
                "is_split": is_split,
            }

        except Exception as e:
            logger.warning(f"TikTok direct extractor exception: {e}")
            return None

    def _download_tiktok_audio(
        self,
        url: str,
        temp_dir: Path,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Direct audio extraction for TikTok via TikWM API."""
        if not requests:
            return None

        try:
            api_url = "https://www.tikwm.com/api/"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.post(api_url, data={"url": url}, headers=headers, timeout=12)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                return None

            tt_data = data["data"]
            vid_id = str(tt_data.get("id") or int(time.time()))
            title = tt_data.get("title") or "TikTok Audio"
            author = (tt_data.get("author") or {}).get("nickname") or "TikTok"
            music_url = tt_data.get("music") or tt_data.get("play")
            if not music_url:
                return None

            # Download audio stream
            raw_audio_path = temp_dir / f"tiktok_{vid_id}.raw"
            r = requests.get(music_url, headers=headers, stream=True, timeout=20)
            if r.status_code != 200:
                return None

            with open(raw_audio_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

            # Download thumbnail for album art
            thumb_file = None
            cover_url = tt_data.get("cover")
            if cover_url:
                try:
                    c_resp = requests.get(cover_url, headers=headers, timeout=8)
                    if c_resp.status_code == 200:
                        thumb_file = temp_dir / f"tiktok_cover_{vid_id}.jpg"
                        with open(thumb_file, "wb") as f:
                            f.write(c_resp.content)
                except Exception:
                    thumb_file = None

            # Convert to standard 192k MP3 using FFmpeg
            clean_title = re.sub(r'[\\/*?:"<>|]', "", title)[:40].strip() or "TikTok_Audio"
            mp3_path = temp_dir / f"{clean_title}.mp3"
            cmd = [
                FFMPEG_EXE,
                "-y",
                "-i", str(raw_audio_path),
                "-vn",
                "-acodec", "libmp3lame",
                "-b:a", "192k",
                str(mp3_path),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
            try:
                raw_audio_path.unlink()
            except Exception:
                pass

            filesize_mb = mp3_path.stat().st_size / (1024 * 1024)
            return {
                "file_path": mp3_path,
                "thumb_file": thumb_file,
                "dir_path": temp_dir,
                "title": title,
                "uploader": author,
                "duration": int(tt_data.get("duration") or 0),
                "filesize_mb": filesize_mb,
            }
        except Exception as e:
            logger.warning(f"TikTok audio direct conversion failed: {e}")
            return None

    def _download_instagram(self, url: str, temp_dir: Path) -> Optional[Dict[str, Any]]:
        """
        Download Instagram posts (single photos, multi-image carousels, mixed albums, or reels)
        using multi-strategy extraction:
        1. Instaloader engine (with session cookies if available)
        2. gallery-dl engine (with session cookies if available)
        3. Instagram public oEmbed API
        4. Logged-out GraphQL Polaris query
        """
        if not requests:
            return None

        m = re.search(r"instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]+)", url) or re.search(
            r"instagr\.am/(?:p|reel|reels)/([A-Za-z0-9_-]+)", url
        )
        if not m:
            return None
        shortcode = m.group(1)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        # Strategy 1: Instaloader Engine (handles photos, multi-image carousels, videos, reels)
        if instaloader:
            try:
                L = instaloader.Instaloader(
                    download_pictures=True,
                    download_videos=True,
                    download_video_thumbnails=False,
                    save_metadata=False,
                    compress_json=False,
                    post_metadata_txt_pattern="",
                    max_connection_attempts=2,
                    dirname_pattern=str(temp_dir),
                )
                if getattr(config, "COOKIES_FILE", None) and Path(config.COOKIES_FILE).exists():
                    try:
                        cj = http.cookiejar.MozillaCookieJar(str(config.COOKIES_FILE))
                        cj.load(ignore_discard=True, ignore_expires=True)
                        for cookie in cj:
                            if "instagram.com" in (cookie.domain or ""):
                                L.context._session.cookies.set_cookie(cookie)
                    except Exception as ce:
                        logger.debug("Failed to inject cookies into Instaloader: %s", ce)

                post = instaloader.Post.from_shortcode(L.context, shortcode)
                caption = post.caption or "Instagram Post"
                author = post.owner_username or "Instagram"

                L.download_post(post, target="media")

                all_files = sorted([f for f in temp_dir.rglob("*") if f.is_file()], key=lambda f: f.name)
                media_files: List[Path] = []
                for f in all_files:
                    if f.suffix.lower() in VIDEO_EXTS:
                        media_files.append(f)
                    elif f.suffix.lower() in IMAGE_EXTS:
                        valid_img = sanitize_image_file(f)
                        if valid_img:
                            media_files.append(valid_img)

                if media_files:
                    if len(media_files) == 1:
                        target = media_files[0]
                        mb = target.stat().st_size / (1024 * 1024)
                        is_vid = target.suffix.lower() in VIDEO_EXTS
                        thumb_file = self._extract_video_thumbnail(target, temp_dir) if is_vid else None
                        return {
                            "type": "video" if is_vid else "photo",
                            "file_path": target,
                            "file_paths": [target],
                            "dir_path": temp_dir,
                            "title": caption,
                            "uploader": author,
                            "duration": 0,
                            "thumbnail": None,
                            "thumb_file": thumb_file,
                            "filesize_mb": mb,
                        }
                    else:
                        mb = sum(f.stat().st_size for f in media_files) / (1024 * 1024)
                        return {
                            "type": "album",
                            "file_path": media_files[0],
                            "file_paths": media_files,
                            "dir_path": temp_dir,
                            "title": caption,
                            "uploader": author,
                            "duration": 0,
                            "thumbnail": None,
                            "filesize_mb": mb,
                        }
            except Exception as e:
                logger.debug("Instaloader extraction failed for %s: %s", shortcode, e)

        # Strategy 2: gallery-dl engine
        try:
            gdl_res = self._download_gallery_dl(url, temp_dir)
            if gdl_res:
                return gdl_res
        except Exception as e:
            logger.debug("gallery-dl instagram extraction failed for %s: %s", shortcode, e)

        # Strategy 3: Official Instagram public oEmbed API
        try:
            oembed_url = f"https://www.instagram.com/api/v1/oembed/?url=https://www.instagram.com/p/{shortcode}/"
            r = requests.get(oembed_url, headers=headers, timeout=12)
            if r.status_code == 200:
                data = r.json()
                caption = data.get("title") or "Instagram Post"
                author = data.get("author_name") or "Instagram"
                thumb_url = data.get("thumbnail_url")
                if thumb_url and "static.cdninstagram.com" not in thumb_url:
                    fpath = temp_dir / f"ig_{shortcode}.jpg"
                    resp = requests.get(thumb_url, headers=headers, timeout=20)
                    if resp.status_code == 200 and resp.content:
                        with open(fpath, "wb") as f:
                            f.write(resp.content)
                        valid_img = sanitize_image_file(fpath)
                        if valid_img:
                            mb = valid_img.stat().st_size / (1024 * 1024)
                            return {
                                "type": "photo",
                                "file_path": valid_img,
                                "file_paths": [valid_img],
                                "dir_path": temp_dir,
                                "title": caption,
                                "uploader": author,
                                "duration": 0,
                                "thumbnail": None,
                                "filesize_mb": mb,
                            }
        except Exception as e:
            logger.debug("Instagram oEmbed API failed for %s: %s", shortcode, e)

        # Strategy 4: Logged-out GraphQL Polaris query
        try:
            variables = {
                "shortcode": shortcode,
                "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
            }
            lsd = secrets.token_hex(12)
            gql_data = {
                "variables": json.dumps(variables, separators=(",", ":")),
                "doc_id": "27128499623469141",
                "server_timestamps": "true",
                "lsd": lsd,
            }
            gql_headers = {
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Fb-Friendly-Name": "PolarisPostRootQuery",
                "X-Fb-Lsd": lsd,
                "X-IG-App-ID": "936619743392459",
            }
            gr = requests.post("https://www.instagram.com/graphql/query/", data=gql_data, headers=gql_headers, timeout=12)
            if gr.status_code == 200 and gr.headers.get("content-type", "").startswith("application/json"):
                gdata = gr.json()
                media_data = gdata.get("data", {}).get("xdt_shortcode_media") or gdata.get("data", {}).get("shortcode_media")
                if media_data:
                    caption = (((media_data.get("edge_media_to_caption") or {}).get("edges") or [{}])[0].get("node") or {}).get("text") or "Instagram Post"
                    author = (media_data.get("owner") or {}).get("username") or "Instagram"
                    edges = (media_data.get("edge_sidecar_to_children") or {}).get("edges") or []
                    items_to_download = []
                    if edges:
                        for edge in edges[:10]:
                            node = edge.get("node") or {}
                            if node.get("is_video") and node.get("video_url"):
                                items_to_download.append(("video", node.get("video_url")))
                            elif node.get("display_url"):
                                items_to_download.append(("photo", node.get("display_url")))
                    else:
                        if media_data.get("is_video") and media_data.get("video_url"):
                            items_to_download.append(("video", media_data.get("video_url")))
                        elif media_data.get("display_url"):
                            items_to_download.append(("photo", media_data.get("display_url")))

                    downloaded: List[Path] = []
                    for idx, (m_type, m_url) in enumerate(items_to_download):
                        ext = ".mp4" if m_type == "video" else ".jpg"
                        fpath = temp_dir / f"ig_gql_{shortcode}_{idx}{ext}"
                        r_dl = requests.get(m_url, headers=headers, stream=(m_type == "video"), timeout=25)
                        if r_dl.status_code == 200:
                            with open(fpath, "wb") as f:
                                for chunk in r_dl.iter_content(65536):
                                    f.write(chunk)
                            if m_type == "video":
                                downloaded.append(fpath)
                            else:
                                v_img = sanitize_image_file(fpath)
                                if v_img:
                                    downloaded.append(v_img)

                    if downloaded:
                        if len(downloaded) == 1:
                            target = downloaded[0]
                            mb = target.stat().st_size / (1024 * 1024)
                            is_vid = target.suffix.lower() in VIDEO_EXTS
                            thumb_file = self._extract_video_thumbnail(target, temp_dir) if is_vid else None
                            return {
                                "type": "video" if is_vid else "photo",
                                "file_path": target,
                                "file_paths": [target],
                                "dir_path": temp_dir,
                                "title": caption,
                                "uploader": author,
                                "duration": 0,
                                "thumbnail": None,
                                "thumb_file": thumb_file,
                                "filesize_mb": mb,
                            }
                        else:
                            mb = sum(f.stat().st_size for f in downloaded) / (1024 * 1024)
                            return {
                                "type": "album",
                                "file_path": downloaded[0],
                                "file_paths": downloaded,
                                "dir_path": temp_dir,
                                "title": caption,
                                "uploader": author,
                                "duration": 0,
                                "thumbnail": None,
                                "filesize_mb": mb,
                            }
        except Exception as e:
            logger.debug("Instagram GraphQL fallback failed for %s: %s", shortcode, e)

        return None

    def _download_reddit(
        self,
        url: str,
        temp_dir: Path,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Specialized extractor for Reddit posts.
        Handles:
          - Direct i.redd.it images
          - Direct v.redd.it videos (via HLSPlaylist m3u8)
          - Video posts with audio merging via RapidSave and direct HLS
          - Multi-image photo albums/galleries and single photos via vxreddit
        """
        if not requests:
            return None

        host = _get_url_host(url)
        clean_url = url.split("?")[0]

        # 1. Direct i.redd.it image or direct image extension
        if host == "i.redd.it" or host.endswith(".i.redd.it") or any(clean_url.lower().endswith(ext) for ext in IMAGE_EXTS):
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    )
                }
                r = requests.get(url, headers=headers, stream=True, timeout=30)
                if r.status_code == 200:
                    ext = Path(clean_url).suffix or ".jpg"
                    fpath = temp_dir / f"reddit_image_{uuid.uuid4().hex[:8]}{ext}"
                    with open(fpath, "wb") as f:
                        for chunk in r.iter_content(65536):
                            f.write(chunk)
                    valid_img = sanitize_image_file(fpath) or fpath
                    mb = valid_img.stat().st_size / (1024 * 1024)
                    return {
                        "type": "photo",
                        "file_path": valid_img,
                        "file_paths": [valid_img],
                        "dir_path": temp_dir,
                        "title": "Reddit Image",
                        "uploader": "Reddit",
                        "duration": 0,
                        "thumbnail": url,
                        "filesize_mb": mb,
                    }
            except Exception as e:
                logger.warning("Direct i.redd.it download failed: %s", e)

        # 2. Direct v.redd.it link
        if "v.redd.it" in host:
            vid = extract_reddit_video_id(url)
            if vid:
                hls_url = f"https://v.redd.it/{vid}/HLSPlaylist.m3u8"
                try:
                    res = self._download_ytdlp(hls_url, temp_dir, progress_callback=progress_callback)
                    if res:
                        res["platform"] = "Reddit"
                        res["title"] = res.get("title") or "Reddit Video"
                        if res["title"] == "HLSPlaylist":
                            res["title"] = "Reddit Video"
                        return res
                except Exception as e:
                    logger.info("Direct v.redd.it HLS download failed: %s", e)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
        }

        # 3. RapidSave video extractor
        try:
            rs_url = f"https://rapidsave.com/info?url={quote(url, safe='')}"
            resp = requests.get(rs_url, headers=headers, timeout=12)
            if resp.status_code == 200 and BeautifulSoup:
                soup = BeautifulSoup(resp.text, "html.parser")
                dl_btn = soup.find("a", class_="downloadbutton")
                if dl_btn and dl_btn.get("href"):
                    dl_href = dl_btn["href"]
                    post_title_el = soup.find("h2", class_="text-truncate") or soup.find("h2") or soup.find("h1")
                    page_title = post_title_el.text.strip() if post_title_el else "Reddit Video"

                    # Try HLS first for best audio/video quality
                    vid = extract_reddit_video_id(dl_href)
                    if vid:
                        hls_url = f"https://v.redd.it/{vid}/HLSPlaylist.m3u8"
                        try:
                            res = self._download_ytdlp(hls_url, temp_dir, progress_callback=progress_callback)
                            if res:
                                res["platform"] = "Reddit"
                                res["title"] = page_title
                                return res
                        except Exception as e:
                            logger.info("yt-dlp via RapidSave HLS failed, trying direct stream: %s", e)

                    # Direct stream from RapidSave
                    target_dl = dl_href if dl_href.startswith("http") else f"https://rapidsave.com{dl_href}"
                    fpath = temp_dir / f"reddit_{uuid.uuid4().hex[:8]}.mp4"
                    r = requests.get(target_dl, headers=headers, stream=True, timeout=60)
                    if r.status_code == 200:
                        with open(fpath, "wb") as f:
                            for chunk in r.iter_content(65536):
                                f.write(chunk)
                        if fpath.exists() and fpath.stat().st_size > 1000:
                            mb = fpath.stat().st_size / (1024 * 1024)
                            thumb_file = self._extract_video_thumbnail(fpath, temp_dir)
                            return {
                                "type": "video",
                                "file_path": fpath,
                                "file_paths": [fpath],
                                "dir_path": temp_dir,
                                "title": page_title,
                                "uploader": "Reddit",
                                "duration": 0,
                                "thumbnail": None,
                                "thumb_file": thumb_file,
                                "filesize_mb": mb,
                            }
        except Exception as rs_err:
            logger.debug("RapidSave extraction failed: %s", rs_err)

        # 4. vxreddit parser (handles albums, single photos, and video fallback)
        try:
            # Map /gallery/<id> to /comments/<id> for vxreddit
            clean_reddit_url = re.sub(r"/gallery/([a-zA-Z0-9]+)", r"/comments/\1", url)
            vx_url = re.sub(r"https?://(?:www\.|old\.)?reddit\.com", "https://vxreddit.com", clean_reddit_url)
            if "vxreddit.com" not in vx_url:
                m_rid = re.search(r"redd\.it/([a-zA-Z0-9]+)", url)
                if m_rid:
                    vx_url = f"https://vxreddit.com/comments/{m_rid.group(1)}"

            vx_headers = {"User-Agent": "TelegramBot (like TwitterBot)"}
            resp = requests.get(vx_url, headers=vx_headers, timeout=12)
            if resp.status_code == 200 and BeautifulSoup:
                soup = BeautifulSoup(resp.text, "html.parser")
                title_meta = soup.find("meta", property="og:title")
                title = title_meta["content"].strip() if title_meta and title_meta.get("content") else "Reddit Post"
                if title == "vxReddit":
                    title = "Reddit Post"

                # Check if it has a video
                vid_meta = soup.find("meta", property="og:video") or soup.find("meta", property="twitter:player:stream")
                if vid_meta and vid_meta.get("content"):
                    vid_content = vid_meta["content"]
                    vid = extract_reddit_video_id(vid_content)
                    if vid:
                        hls_url = f"https://v.redd.it/{vid}/HLSPlaylist.m3u8"
                        try:
                            res = self._download_ytdlp(hls_url, temp_dir, progress_callback=progress_callback)
                            if res:
                                res["platform"] = "Reddit"
                                res["title"] = title
                                return res
                        except Exception:
                            pass

                    # Stream direct video
                    fpath = temp_dir / f"reddit_video_{uuid.uuid4().hex[:8]}.mp4"
                    r_vid = requests.get(vid_content, headers=vx_headers, stream=True, timeout=60)
                    if r_vid.status_code == 200:
                        with open(fpath, "wb") as f:
                            for chunk in r_vid.iter_content(65536):
                                f.write(chunk)
                        if fpath.exists() and fpath.stat().st_size > 1000:
                            mb = fpath.stat().st_size / (1024 * 1024)
                            thumb_file = self._extract_video_thumbnail(fpath, temp_dir)
                            return {
                                "type": "video",
                                "file_path": fpath,
                                "file_paths": [fpath],
                                "dir_path": temp_dir,
                                "title": title,
                                "uploader": "Reddit",
                                "duration": 0,
                                "thumbnail": None,
                                "thumb_file": thumb_file,
                                "filesize_mb": mb,
                            }

                # Check images
                img_metas = soup.find_all("meta", property="og:image")
                img_urls = []
                for im in img_metas:
                    c = im.get("content")
                    if c and c not in img_urls and not any(skip in c for skip in ("redditstatic", "favicon")):
                        img_urls.append(c)

                if img_urls:
                    downloaded_images: List[Path] = []
                    for idx, i_url in enumerate(img_urls):
                        try:
                            ext = Path(i_url.split("?")[0]).suffix or ".jpg"
                            fpath = temp_dir / f"reddit_{idx}_{uuid.uuid4().hex[:6]}{ext}"
                            r_img = requests.get(i_url, headers=headers, timeout=20)
                            if r_img.status_code == 200 and r_img.content:
                                fpath.write_bytes(r_img.content)
                                valid = sanitize_image_file(fpath)
                                if valid:
                                    downloaded_images.append(valid)
                        except Exception as e:
                            logger.warning("Error downloading reddit image %s: %s", i_url, e)

                    if len(downloaded_images) == 1:
                        mb = downloaded_images[0].stat().st_size / (1024 * 1024)
                        return {
                            "type": "photo",
                            "file_path": downloaded_images[0],
                            "file_paths": downloaded_images,
                            "dir_path": temp_dir,
                            "title": title,
                            "uploader": "Reddit",
                            "duration": 0,
                            "thumbnail": img_urls[0],
                            "filesize_mb": mb,
                        }
                    elif len(downloaded_images) > 1:
                        mb = sum(f.stat().st_size for f in downloaded_images) / (1024 * 1024)
                        return {
                            "type": "album",
                            "file_path": downloaded_images[0],
                            "file_paths": downloaded_images,
                            "dir_path": temp_dir,
                            "title": title,
                            "uploader": "Reddit",
                            "duration": 0,
                            "thumbnail": img_urls[0],
                            "filesize_mb": mb,
                        }
        except Exception as vx_err:
            logger.debug("vxreddit extraction failed: %s", vx_err)

        return None

    def _download_reddit_audio(
        self,
        url: str,
        temp_dir: Path,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Download Reddit video post audio as MP3."""
        vid = extract_reddit_video_id(url)
        if not vid and requests:
            try:
                rs_url = f"https://rapidsave.com/info?url={quote(url, safe='')}"
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    )
                }
                resp = requests.get(rs_url, headers=headers, timeout=10)
                if resp.status_code == 200 and BeautifulSoup:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    dl_btn = soup.find("a", class_="downloadbutton")
                    if dl_btn and dl_btn.get("href"):
                        vid = extract_reddit_video_id(dl_btn["href"])
            except Exception:
                pass

        if not vid and requests:
            try:
                vx_url = re.sub(r"https?://(?:www\.|old\.)?reddit\.com", "https://vxreddit.com", url)
                resp = requests.get(vx_url, headers={"User-Agent": "TelegramBot"}, timeout=10)
                if resp.status_code == 200 and BeautifulSoup:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    vid_meta = soup.find("meta", property="og:video") or soup.find("meta", property="twitter:player:stream")
                    if vid_meta and vid_meta.get("content"):
                        vid = extract_reddit_video_id(vid_meta["content"])
            except Exception:
                pass

        if vid and yt_dlp:
            hls_url = f"https://v.redd.it/{vid}/HLSPlaylist.m3u8"
            ydl_opts = self._get_ydl_base_opts()
            ydl_opts.update({
                "paths": {"home": temp_dir.as_posix()},
                "outtmpl": {"default": "%(id).30s.%(ext)s"},
                "format": "bestaudio/best",
                "writethumbnail": False,
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

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(hls_url, download=True)
                mp3_files = list(temp_dir.glob("*.mp3"))
                if mp3_files:
                    fpath = mp3_files[0]
                    mb = fpath.stat().st_size / (1024 * 1024)
                    return {
                        "type": "audio",
                        "file_path": fpath,
                        "file_paths": [fpath],
                        "dir_path": temp_dir,
                        "title": "Reddit Audio",
                        "uploader": "Reddit",
                        "duration": int(info.get("duration") or 0),
                        "thumbnail": None,
                        "filesize_mb": mb,
                    }
        return None

    def _download_gallery_dl(self, url: str, temp_dir: Path) -> Optional[Dict[str, Any]]:
        """Fallback extractor for image posts/galleries/carousels using gallery-dl."""
        try:
            user_gdl = Path.home() / ".local" / "bin" / "gallery-dl"
            if shutil.which("gallery-dl"):
                cmd = [shutil.which("gallery-dl"), "--dest", str(temp_dir), "--no-mtime"]
            elif user_gdl.exists():
                cmd = [str(user_gdl), "--dest", str(temp_dir), "--no-mtime"]
            else:
                cmd = [sys.executable, "-m", "gallery_dl", "--dest", str(temp_dir), "--no-mtime"]

            if getattr(config, "COOKIES_FILE", None) and Path(config.COOKIES_FILE).exists():
                cmd.extend(["--cookies", str(config.COOKIES_FILE)])

            cmd.append(url)
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

            all_files = sorted([f for f in temp_dir.rglob("*") if f.is_file()], key=lambda f: f.name)
            vids = [f for f in all_files if f.suffix.lower() in VIDEO_EXTS]
            raw_imgs = [f for f in all_files if f.suffix.lower() in IMAGE_EXTS]
            sanitized_imgs = []
            for img in raw_imgs:
                s = sanitize_image_file(img)
                if s:
                    sanitized_imgs.append(s)

            media_files = sorted(vids + sanitized_imgs, key=lambda f: f.name)
            if not media_files:
                return None

            uploader = detect_platform(url)
            if len(media_files) == 1:
                target = media_files[0]
                mb = target.stat().st_size / (1024 * 1024)
                is_vid = target.suffix.lower() in VIDEO_EXTS
                thumb_file = self._extract_video_thumbnail(target, temp_dir) if is_vid else None
                return {
                    "type": "video" if is_vid else "photo",
                    "file_path": target,
                    "file_paths": [target],
                    "dir_path": temp_dir,
                    "title": f"{uploader} Post",
                    "uploader": uploader,
                    "duration": 0,
                    "thumbnail": None,
                    "thumb_file": thumb_file,
                    "filesize_mb": mb,
                }
            else:
                mb = sum(f.stat().st_size for f in media_files) / (1024 * 1024)
                return {
                    "type": "album",
                    "file_path": media_files[0],
                    "file_paths": media_files,
                    "dir_path": temp_dir,
                    "title": f"{uploader} Album",
                    "uploader": uploader,
                    "duration": 0,
                    "thumbnail": None,
                    "filesize_mb": mb,
                }
        except Exception as e:
            logger.warning(f"gallery-dl error: {e}")

        return None

    def _compress_video_to_fit(self, input_path: Path, temp_dir: Path, duration: float) -> Optional[Path]:
        """
        Compress video using FFmpeg to fit under the configured maximum limit.
        Calculates optimal video & audio bitrates based on duration.
        """
        if duration <= 0:
            try:
                cmd = [FFMPEG_EXE, "-i", str(input_path)]
                proc = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, timeout=10)
                match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
                if match:
                    hours, mins, secs = match.groups()
                    duration = int(hours) * 3600 + int(mins) * 60 + float(secs)
            except Exception:
                duration = 600

        if duration <= 0:
            duration = 600

        # Don't attempt to compress ultra-long videos (> 45 minutes) as quality degrades too much
        if duration > 2700:
            return None

        output_path = temp_dir / f"compressed_{input_path.stem}.mp4"

        # Target size safely under configured limit
        target_mb = 45.5 if config.MAX_FILE_SIZE_MB <= 50 else min(config.MAX_FILE_SIZE_MB - 5.0, 1950.0)
        target_size_bits = target_mb * 1024 * 1024 * 8
        total_bitrate = int(target_size_bits / duration)

        audio_bitrate = min(96000, max(48000, int(total_bitrate * 0.15)))
        video_bitrate = max(150000, total_bitrate - audio_bitrate)

        # Scale resolution down to 480p or 720p if needed to preserve visual quality
        scale_filter = "scale='min(854,iw)':-2" if video_bitrate < 650000 else "scale='min(1280,iw)':-2"

        cmd = [
            FFMPEG_EXE,
            "-y",
            "-i", str(input_path),
            "-c:v", "libx264",
            "-b:v", f"{video_bitrate}",
            "-maxrate", f"{int(video_bitrate * 1.3)}",
            "-bufsize", f"{int(video_bitrate * 2)}",
            "-vf", scale_filter,
            "-preset", "ultrafast",
            "-threads", "0",
            "-c:a", "aac",
            "-b:a", f"{audio_bitrate}",
            "-movflags", "+faststart",
            str(output_path),
        ]

        try:
            logger.info("Compressing video (%s) to fit %.0fMB (bitrate: %d bps)...", input_path.name, config.MAX_FILE_SIZE_MB, video_bitrate)
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120)
            if output_path.exists() and output_path.stat().st_size > 0:
                compressed_size_mb = output_path.stat().st_size / (1024 * 1024)
                if compressed_size_mb <= (config.MAX_FILE_SIZE_MB - 0.5):
                    logger.info("Auto-compression succeeded: %.1f MB", compressed_size_mb)
                    return output_path
        except Exception as e:
            logger.warning("Auto-compression failed: %s", e)

        return None

    def _split_video_into_parts(
        self,
        input_path: Path,
        temp_dir: Path,
        target_part_size_mb: Optional[float] = None,
        duration: float = 0,
    ) -> List[Path]:
        """
        Split an oversized video into seamless playable segments using FFmpeg stream copy (-c copy).
        Runs in ~1-2 seconds without re-encoding.
        """
        if not input_path.exists():
            return []

        limit_mb = target_part_size_mb or (config.MAX_FILE_SIZE_MB - 2.0)
        filesize_mb = input_path.stat().st_size / (1024 * 1024)
        if filesize_mb <= limit_mb:
            return [input_path]

        if duration <= 0:
            try:
                cmd = [FFMPEG_EXE, "-i", str(input_path)]
                proc = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, timeout=10)
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
                if m:
                    h, mn, s = m.groups()
                    duration = int(h) * 3600 + int(mn) * 60 + float(s)
            except Exception:
                duration = 0

        num_parts = max(2, int(math.ceil(filesize_mb / limit_mb)))
        segment_time = max(30, int((duration / num_parts) * 0.95)) if duration > 0 else 300

        split_pattern = temp_dir / f"part_%03d_{input_path.name}"
        cmd = [
            FFMPEG_EXE,
            "-y",
            "-i", str(input_path),
            "-c", "copy",
            "-map", "0",
            "-f", "segment",
            "-segment_time", str(segment_time),
            "-reset_timestamps", "1",
            str(split_pattern),
        ]

        try:
            logger.info("Splitting %s into parts (~%d sec segments) to fit limit...", input_path.name, segment_time)
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=180)
            parts = sorted(list(temp_dir.glob(f"part_*_{input_path.name}")))
            if parts:
                logger.info("Successfully split video into %d parts", len(parts))
                return parts
        except Exception as e:
            logger.warning("Video splitting failed: %s", e)

        return [input_path]

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
            # Respect the selected resolution, but do not filter by individual
            # stream filesize: separate video/audio streams can exceed that
            # threshold even when the final merged file is suitable.
            format_spec = (
                f"bestvideo[height<={resolution}]+bestaudio/"
                f"best[height<={resolution}]"
            )
        else:
            # Download the source's highest available stream
            format_spec = "bestvideo+bestaudio/best"

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

        is_youtube = ("youtube.com" in url.lower() or "youtu.be" in url.lower())

        attempts = [{"use_cookies": True, "clients": None}]
        if is_youtube:
            attempts.append({"use_cookies": False, "clients": None})
            attempts.append({"use_cookies": False, "clients": ["web", "mweb"]})
            attempts.append({"use_cookies": False, "clients": ["tv_embedded", "android", "ios"]})

        info = None
        ytdl_dl_err = None

        for att_idx, att in enumerate(attempts):
            cur_opts = self._get_ydl_base_opts(
                use_cookies=att["use_cookies"],
                youtube_clients=att["clients"],
            )
            cur_opts.update({
                "paths": {"home": temp_dir.as_posix()},
                "outtmpl": {"default": "%(id).30s.%(ext)s"},
                "format": format_spec,
            })
            if progress_callback:
                cur_opts["progress_hooks"] = ydl_opts.get("progress_hooks", [])

            try:
                with yt_dlp.YoutubeDL(cur_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                ytdl_dl_err = None
                break
            except Exception as err:
                ytdl_dl_err = err
                err_str = str(err).lower()
                is_retryable = any(
                    k in err_str
                    for k in (
                        "sign in",
                        "bot",
                        "confirm you",
                        "cookie",
                        "login",
                        "429",
                        "400",
                        "precondition check failed",
                        "failed to extract any player response",
                        "requested format is not available",
                        "unable to download api page",
                        "http error",
                        "unavailable",
                    )
                )
                if is_youtube and is_retryable and att_idx < len(attempts) - 1:
                    logger.warning(
                        "YouTube attempt %d failed (%s); retrying with alternative client...",
                        att_idx + 1,
                        err,
                    )
                    continue
                break

        if ytdl_dl_err:
            # If download failed because there are no video formats (e.g. photo post or image carousel),
            # probe metadata without stream requirement and download photos/slides directly
            if requests and ("no video format" in str(ytdl_dl_err).lower() or "formats" in str(ytdl_dl_err).lower()):
                logger.info("No video formats found in yt-dlp, attempting image/carousel metadata download...")
                try:
                    opts_meta = self._get_ydl_base_opts()
                    opts_meta["skip_download"] = True
                    with yt_dlp.YoutubeDL(opts_meta) as ydl_meta:
                        info = ydl_meta.extract_info(url, download=False)
                    entries = (info or {}).get("entries")
                    if entries:
                        for idx, entry in enumerate(entries[:10]):
                            img_u = entry.get("url") or entry.get("thumbnail")
                            if img_u:
                                ext = ".mp4" if entry.get("vcodec") != "none" and ".mp4" in img_u.lower() else ".jpg"
                                fpath = temp_dir / f"post_{idx}{ext}"
                                r = requests.get(img_u, timeout=25)
                                if r.status_code == 200 and r.content:
                                    with open(fpath, "wb") as f:
                                        f.write(r.content)
                    elif info and (info.get("url") or info.get("thumbnail")):
                        img_u = info.get("url") or info.get("thumbnail")
                        fpath = temp_dir / "post_photo.jpg"
                        r = requests.get(img_u, timeout=25)
                        if r.status_code == 200 and r.content:
                            with open(fpath, "wb") as f:
                                f.write(r.content)
                except Exception:
                    pass
            if not list(temp_dir.glob("*")):
                raise ytdl_dl_err

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
        duration = (info or {}).get("duration", 0)
        is_compressed = False
        is_split = False

        # Check Telegram file limit: attempt optional compression or zero-loss auto-splitting
        if filesize_mb > config.MAX_FILE_SIZE_MB:
            if media_type == "video":
                if config.ENABLE_VIDEO_COMPRESSION:
                    logger.info(
                        "Video size (%.1f MB) exceeds limit (%.0f MB); attempting compression...",
                        filesize_mb,
                        config.MAX_FILE_SIZE_MB,
                    )
                    compressed = self._compress_video_to_fit(target_file, temp_dir, duration)
                    if compressed and compressed.exists():
                        target_file = compressed
                        out_files = [target_file]
                        filesize_mb = target_file.stat().st_size / (1024 * 1024)
                        is_compressed = True

                if not is_compressed and config.AUTO_SPLIT_OVERSIZED_VIDEOS:
                    split_parts = self._split_video_into_parts(target_file, temp_dir, duration=duration)
                    if len(split_parts) > 1:
                        out_files = split_parts
                        target_file = split_parts[0]
                        is_split = True
                        logger.info("Auto-split video into %d parts", len(split_parts))
                    else:
                        dur_mins = int(duration // 60) if duration else 0
                        dur_msg = f" (~{dur_mins} mins)" if dur_mins > 0 else ""
                        raise DownloaderError(
                            f"Original video is {filesize_mb:.1f} MB, which exceeds the {config.MAX_FILE_SIZE_MB:.0f} MB limit{dur_msg}."
                        )
                elif not is_compressed:
                    dur_mins = int(duration // 60) if duration else 0
                    dur_msg = f" (~{dur_mins} mins)" if dur_mins > 0 else ""
                    raise DownloaderError(
                        f"Original video is {filesize_mb:.1f} MB, which exceeds the {config.MAX_FILE_SIZE_MB:.0f} MB limit{dur_msg}."
                    )
            else:
                raise DownloaderError(
                    f"Media size is {filesize_mb:.1f} MB, which exceeds the {config.MAX_FILE_SIZE_MB:.0f} MB limit."
                )

        title = (info or {}).get("title", "Video")
        uploader = (info or {}).get("uploader") or (info or {}).get("channel", "Unknown")
        thumbnail = (info or {}).get("thumbnail")

        # Generate a video thumbnail for fast Telegram player preview
        thumb_file = None
        if media_type == "video" and target_file.exists():
            thumb_file = self._extract_video_thumbnail(target_file, temp_dir)

        width = (info or {}).get("width")
        height = (info or {}).get("height")

        return {
            "type": media_type,
            "file_path": target_file,
            "file_paths": out_files,
            "dir_path": temp_dir,
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "thumbnail": thumbnail,
            "thumb_file": thumb_file,
            "width": width,
            "height": height,
            "is_compressed": is_compressed,
            "is_split": is_split,
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
        Routes to Twitter fast extractor, yt-dlp (YouTube, Pinterest video, Instagram, TikTok, etc.),
        and specialized fallbacks (Pinterest photo downloader, gallery-dl).
        """
        temp_dir = Path(tempfile.mkdtemp(dir=str(self.download_dir)))

        # Resolve Pinterest pin.it redirects upfront
        if is_pinterest_url(url):
            url = resolve_pinterest_url(url)

        try:
            # 1. Specialized TeraBox Downloader
            if is_terabox_url(url):
                return await asyncio.to_thread(self._download_terabox, url, temp_dir, progress_callback)

            # 2. Specialized DiskWala Downloader
            if is_diskwala_url(url):
                return await asyncio.to_thread(self._download_diskwala, url, temp_dir, progress_callback)

            # 3. Specialized Twitter / X Downloader (direct photo/video/album extraction)
            if is_twitter_url(url):
                try:
                    tw_res = await asyncio.to_thread(self._download_twitter, url, temp_dir)
                    if tw_res:
                        return tw_res
                except Exception as e:
                    logger.warning(f"Twitter engine error, trying fallback: {e}")

            # 4. Specialized TikTok Downloader (watermark-free direct API with regional ISP bypass)
            if is_tiktok_url(url):
                try:
                    tt_res = await asyncio.to_thread(self._download_tiktok, url, temp_dir, progress_callback)
                    if tt_res:
                        return tt_res
                except Exception as e:
                    logger.warning(f"TikTok direct engine error, trying fallback: {e}")

            # 5. Specialized Instagram Downloader (photos, multi-image carousels, reels)
            if is_instagram_url(url):
                try:
                    ig_res = await asyncio.to_thread(self._download_instagram, url, temp_dir)
                    if ig_res:
                        return ig_res
                except Exception as e:
                    logger.warning(f"Instagram engine error, trying fallback: {e}")

            # 6. Specialized Reddit Downloader (videos with merged audio, single images, photo galleries)
            if is_reddit_url(url):
                try:
                    red_res = await asyncio.to_thread(self._download_reddit, url, temp_dir, progress_callback)
                    if red_res:
                        return red_res
                except Exception as e:
                    logger.warning(f"Reddit specialized extractor error, trying fallback: {e}")

            # 7. Main yt-dlp download (handles YouTube, Instagram, TikTok, Reddit, Pinterest, Facebook, etc.)
            try:
                return await asyncio.to_thread(self._download_ytdlp, url, temp_dir, resolution, progress_callback)
            except Exception as ytdl_err:
                logger.info(f"yt-dlp attempt failed for {url}: {ytdl_err}")

                # 4. Fallback to specialized Pinterest handler (for image pins or pins with no video streams)
                if is_pinterest_url(url) and Pinterest:
                    try:
                        return await asyncio.to_thread(self._download_pinterest, url, temp_dir)
                    except Exception as pin_err:
                        logger.warning(f"Pinterest fallback error: {pin_err}")

                # 5. Secondary fallback to Instagram extractor
                if is_instagram_url(url):
                    try:
                        ig_res = await asyncio.to_thread(self._download_instagram, url, temp_dir)
                        if ig_res:
                            return ig_res
                    except Exception as ig_err:
                        logger.warning(f"Instagram fallback error: {ig_err}")

                # 6. Fallback to gallery-dl for posts without video streams (Instagram photos, carousels, Pinterest images)
                gdl_res = await asyncio.to_thread(self._download_gallery_dl, url, temp_dir)
                if gdl_res:
                    return gdl_res

                raise DownloaderError(f"Download failed: {ytdl_err}")

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
        if is_pinterest_url(url):
            url = resolve_pinterest_url(url)

        temp_dir = Path(tempfile.mkdtemp(dir=str(self.download_dir)))

        # Specialized TikTok direct audio extraction (bypasses ISP blocks)
        if is_tiktok_url(url):
            try:
                tt_audio = await asyncio.to_thread(self._download_tiktok_audio, url, temp_dir, progress_callback)
                if tt_audio:
                    return tt_audio
            except Exception as e:
                logger.warning(f"TikTok direct audio extraction error: {e}")

        # Specialized Reddit direct audio extraction
        if is_reddit_url(url):
            try:
                red_audio = await asyncio.to_thread(self._download_reddit_audio, url, temp_dir, progress_callback)
                if red_audio:
                    return red_audio
            except Exception as e:
                logger.warning(f"Reddit direct audio extraction error: {e}")

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
            is_youtube = ("youtube.com" in url.lower() or "youtu.be" in url.lower())
            attempts = [{"use_cookies": True, "clients": None}]
            if is_youtube:
                attempts.append({"use_cookies": False, "clients": None})
                attempts.append({"use_cookies": False, "clients": ["web", "mweb"]})
                attempts.append({"use_cookies": False, "clients": ["tv_embedded", "android", "ios"]})

            for att_idx, att in enumerate(attempts):
                cur_opts = self._get_ydl_base_opts(
                    use_cookies=att["use_cookies"],
                    youtube_clients=att["clients"],
                )
                cur_opts.update({
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
                    cur_opts["progress_hooks"] = ydl_opts.get("progress_hooks", [])

                try:
                    with yt_dlp.YoutubeDL(cur_opts) as ydl:
                        return ydl.extract_info(url, download=True)
                except Exception as e:
                    err_str = str(e).lower()
                    is_retryable = any(
                        k in err_str
                        for k in (
                            "sign in",
                            "bot",
                            "confirm you",
                            "cookie",
                            "login",
                            "429",
                            "400",
                            "precondition check failed",
                            "failed to extract any player response",
                            "requested format is not available",
                            "unable to download api page",
                            "http error",
                            "unavailable",
                        )
                    )
                    if is_youtube and is_retryable and att_idx < len(attempts) - 1:
                        logger.warning("Audio download attempt failed (%s), retrying with alternative client...", e)
                        continue
                    raise

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

        if filesize_mb > config.MAX_FILE_SIZE_MB:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloaderError(f"Audio file is too large for Telegram ({filesize_mb:.1f} MB > {config.MAX_FILE_SIZE_MB:.0f} MB limit).")

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
