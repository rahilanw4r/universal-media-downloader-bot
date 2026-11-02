import asyncio
import os
import re
import sys
import html
import shutil
import tempfile
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

try:
    from PIL import Image
except ImportError:
    Image = None

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


def resolve_pinterest_url(url: str) -> str:
    """Resolve shortened pin.it redirects to the canonical pinterest.com URL."""
    url_lower = url.lower()
    if "pin.it" in url_lower:
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
    """Check if URL belongs to X / Twitter."""
    url_lower = url.lower()
    return "twitter.com" in url_lower or "x.com" in url_lower


def is_instagram_url(url: str) -> bool:
    """Check if URL belongs to Instagram."""
    url_lower = url.lower()
    return "instagram.com" in url_lower or "instagr.am" in url_lower


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
            clean_path = image_path.with_suffix(".clean.jpg")
            img.save(clean_path, "JPEG", quality=92, optimize=True)
            # Replace original with clean file
            image_path.unlink(missing_ok=True)
            clean_path.rename(image_path)
            return image_path
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

        # YouTube client alignment:
        # If desktop cookies are present, use web clients matching the desktop session.
        # If no cookies or fallback requested, use ios/android mobile clients which bypass bot gates.
        if youtube_clients:
            clients = youtube_clients
        elif has_cookies:
            clients = ["web", "mweb"]
        else:
            clients = ["ios", "android", "mweb"]

        opts["extractor_args"] = {
            "youtube": {
                "player_client": clients,
                "player_skip": ["webpage", "configs"],
            }
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
        if filesize_mb > 50:
            raise DownloaderError(f"File is too large for Telegram ({filesize_mb:.1f} MB > 50 MB limit).")

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

    def _download_instagram(self, url: str, temp_dir: Path) -> Optional[Dict[str, Any]]:
        """
        Download Instagram posts (single photos, multi-image carousels, mixed albums, or reels)
        using multi-strategy extraction:
        1. Direct API extraction via DDInstagram proxy
        2. Instagram public embed page scraping
        3. yt-dlp metadata extraction fallback
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

        # Strategy 1: ddinstagram proxy API (handles photos, multi-image carousels, and videos)
        try:
            api_url = f"https://api.ddinstagram.com/p/{shortcode}"
            r = requests.get(api_url, headers=headers, timeout=12)
            if r.status_code == 200:
                data = r.json()
                item = data.get("item") or {}
                caption = item.get("caption") or "Instagram Post"
                author = (item.get("user") or {}).get("username") or "Instagram"
                media_list: List[tuple] = []

                # Handle carousel/multi-media
                carousel = item.get("carousel") or item.get("media_details") or []
                if carousel:
                    for c_item in carousel:
                        m_url = c_item.get("url") or c_item.get("download_url")
                        m_type = c_item.get("type") or ("video" if ".mp4" in (m_url or "").lower() else "photo")
                        if m_url:
                            media_list.append((m_type, m_url))
                elif item.get("images"):
                    for img_url in item.get("images"):
                        media_list.append(("photo", img_url))
                elif item.get("videos"):
                    for vid_url in item.get("videos"):
                        media_list.append(("video", vid_url))
                elif item.get("video_url"):
                    media_list.append(("video", item.get("video_url")))
                elif item.get("image_url"):
                    media_list.append(("photo", item.get("image_url")))

                if media_list:
                    downloaded_files: List[Path] = []
                    for idx, (m_type, m_url) in enumerate(media_list[:10]):
                        ext = ".mp4" if m_type == "video" or ".mp4" in m_url.lower() else ".jpg"
                        fpath = temp_dir / f"ig_{shortcode}_{idx}{ext}"
                        resp = requests.get(m_url, headers=headers, stream=True, timeout=25)
                        if resp.status_code == 200:
                            with open(fpath, "wb") as f:
                                for chunk in resp.iter_content(65536):
                                    if chunk:
                                        f.write(chunk)
                            if fpath.exists() and fpath.stat().st_size > 0:
                                if ext == ".mp4":
                                    downloaded_files.append(fpath)
                                else:
                                    valid_img = sanitize_image_file(fpath)
                                    if valid_img:
                                        downloaded_files.append(valid_img)

                    if downloaded_files:
                        if len(downloaded_files) == 1:
                            target = downloaded_files[0]
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
                            mb = sum(f.stat().st_size for f in downloaded_files) / (1024 * 1024)
                            return {
                                "type": "album",
                                "file_path": downloaded_files[0],
                                "file_paths": downloaded_files,
                                "dir_path": temp_dir,
                                "title": caption,
                                "uploader": author,
                                "duration": 0,
                                "thumbnail": None,
                                "filesize_mb": mb,
                            }
        except Exception as e:
            logger.debug("Instagram DDInstagram API error: %s", e)

        # Strategy 2: Instagram Public Embed Page Scraping
        try:
            embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
            r = requests.get(embed_url, headers=headers, timeout=12)
            if r.status_code == 200 and r.text:
                html_text = r.text

                # Check for video in embed
                vid_matches = re.findall(r'class="EmbeddedMediaVideo"[^>]*src="([^"]+)"', html_text) or re.findall(
                    r'"video_url":"([^"]+)"', html_text
                )
                if vid_matches:
                    vid_url = vid_matches[0].replace(r"\/", "/").replace("&amp;", "&")
                    fpath = temp_dir / f"ig_{shortcode}.mp4"
                    resp = requests.get(vid_url, headers=headers, stream=True, timeout=30)
                    if resp.status_code == 200:
                        with open(fpath, "wb") as f:
                            for chunk in resp.iter_content(65536):
                                f.write(chunk)
                        if fpath.exists() and fpath.stat().st_size > 0:
                            mb = fpath.stat().st_size / (1024 * 1024)
                            thumb_file = self._extract_video_thumbnail(fpath, temp_dir)
                            return {
                                "type": "video",
                                "file_path": fpath,
                                "file_paths": [fpath],
                                "dir_path": temp_dir,
                                "title": "Instagram Video",
                                "uploader": "Instagram",
                                "duration": 0,
                                "thumbnail": None,
                                "thumb_file": thumb_file,
                                "filesize_mb": mb,
                            }

                # Check for images in embed
                img_matches = re.findall(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', html_text) or re.findall(
                    r'"display_url":"([^"]+)"', html_text
                )
                if not img_matches:
                    cdn_matches = re.findall(r'(https://[^"\'\s<>]*cdninstagram\.com/[^"\'\s<>]*)', html_text)
                    img_matches = [u for u in cdn_matches if "150x150" not in u and "s150x150" not in u]

                if img_matches:
                    seen_urls = set()
                    unique_img_urls = []
                    for u in img_matches:
                        clean_u = html.unescape(u.replace(r"\/", "/"))
                        # Filter out icon / thumbnail sizes and non-image media
                        if any(bad in clean_u.lower() for bad in ("150x150", "s150x150", "s320x320", ".mp4", ".m4a")):
                            continue
                        if clean_u not in seen_urls:
                            seen_urls.add(clean_u)
                            unique_img_urls.append(clean_u)

                    downloaded_files: List[Path] = []
                    for idx, img_url in enumerate(unique_img_urls[:10]):
                        fpath = temp_dir / f"ig_{shortcode}_{idx}.jpg"
                        resp = requests.get(img_url, headers=headers, timeout=20)
                        if resp.status_code == 200 and resp.content:
                            with open(fpath, "wb") as f:
                                f.write(resp.content)
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
                            "title": "Instagram Post",
                            "uploader": "Instagram",
                            "duration": 0,
                            "thumbnail": None,
                            "filesize_mb": mb,
                        }
                    elif len(downloaded_files) > 1:
                        mb = sum(f.stat().st_size for f in downloaded_files) / (1024 * 1024)
                        return {
                            "type": "album",
                            "file_path": downloaded_files[0],
                            "file_paths": downloaded_files,
                            "dir_path": temp_dir,
                            "title": "Instagram Gallery",
                            "uploader": "Instagram",
                            "duration": 0,
                            "thumbnail": None,
                            "filesize_mb": mb,
                        }
        except Exception as e:
            logger.debug("Instagram embed scraping error: %s", e)

        return None

    def _download_gallery_dl(self, url: str, temp_dir: Path) -> Optional[Dict[str, Any]]:
        """Fallback extractor for image posts/galleries using gallery-dl."""
        try:
            gdl_bin = shutil.which("gallery-dl")
            if gdl_bin:
                cmd = [gdl_bin, "--dest", str(temp_dir), "--no-mtime"]
            else:
                cmd = [sys.executable, "-m", "gallery_dl", "--dest", str(temp_dir), "--no-mtime"]

            if getattr(config, "COOKIES_FILE", None) and Path(config.COOKIES_FILE).exists():
                cmd.extend(["--cookies", str(config.COOKIES_FILE)])

            cmd.append(url)
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
        if is_pinterest_url(url):
            url = resolve_pinterest_url(url)

        def _probe():
            is_youtube = ("youtube.com" in url.lower() or "youtu.be" in url.lower())
            attempts = [{"use_cookies": True, "clients": None}]
            if is_youtube:
                attempts.append({"use_cookies": False, "clients": ["ios", "android"]})

            for att in attempts:
                try:
                    opts = self._get_ydl_base_opts(
                        use_cookies=att["use_cookies"],
                        youtube_clients=att["clients"],
                    )
                    opts.update({"skip_download": True})
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        return ydl.extract_info(url, download=False)
                except Exception:
                    continue
            return {}

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

    def _compress_video_to_fit(self, input_path: Path, temp_dir: Path, duration: float) -> Optional[Path]:
        """
        Compress video using FFmpeg to fit under Telegram's 50 MB limit (~46 MB target).
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

        # Target: 45.5 MB (safe margin for Telegram 50 MB limit)
        target_size_bits = 45.5 * 1024 * 1024 * 8
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
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", f"{audio_bitrate}",
            "-movflags", "+faststart",
            str(output_path),
        ]

        try:
            logger.info("Compressing video (%s) to fit 50MB (bitrate: %d bps)...", input_path.name, video_bitrate)
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=300)
            if output_path.exists() and output_path.stat().st_size > 0:
                compressed_size_mb = output_path.stat().st_size / (1024 * 1024)
                if compressed_size_mb <= 49.5:
                    logger.info("Auto-compression succeeded: %.1f MB", compressed_size_mb)
                    return output_path
        except Exception as e:
            logger.warning("Auto-compression failed: %s", e)

        return None

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
            # Instant mode should start from the source's highest available
            # quality. If the merged file is too large for Telegram, the
            # size-aware compression step below handles it.
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
            attempts.append({"use_cookies": False, "clients": ["ios", "android"]})
            attempts.append({"use_cookies": False, "clients": ["mweb", "tv"]})

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
                is_cookie_or_bot = any(
                    k in err_str
                    for k in (
                        "sign in",
                        "bot",
                        "confirm you",
                        "cookie",
                        "login",
                        "429",
                        "unavailable",
                        "private",
                    )
                )
                if is_youtube and is_cookie_or_bot and att_idx < len(attempts) - 1:
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

        # Check Telegram 50 MB limit: attempt smart compression if video exceeds limit
        if filesize_mb > 50:
            if media_type == "video":
                logger.info(
                    "Video size (%.1f MB) exceeds Telegram 50 MB limit; attempting auto-compression...",
                    filesize_mb,
                )
                compressed = self._compress_video_to_fit(target_file, temp_dir, duration)
                if compressed and compressed.exists():
                    target_file = compressed
                    out_files = [target_file]
                    filesize_mb = target_file.stat().st_size / (1024 * 1024)
                    is_compressed = True
                else:
                    dur_mins = int(duration // 60) if duration else 0
                    dur_msg = f" (~{dur_mins} mins)" if dur_mins > 0 else ""
                    raise DownloaderError(
                        f"Original video is {filesize_mb:.1f} MB, which exceeds Telegram's 50 MB bot limit{dur_msg}, "
                        f"and could not be compressed without severe quality degradation."
                    )
            else:
                raise DownloaderError(
                    f"Media size is {filesize_mb:.1f} MB, which exceeds Telegram's 50 MB bot upload limit."
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
            # 1. Specialized Twitter / X Downloader (instant direct photo/video/album extraction)
            if is_twitter_url(url):
                try:
                    tw_res = await asyncio.to_thread(self._download_twitter, url, temp_dir)
                    if tw_res:
                        return tw_res
                except Exception as e:
                    logger.warning(f"Twitter engine error, trying fallback: {e}")

            # 2. Specialized Instagram Downloader (photos, multi-image carousels, reels)
            if is_instagram_url(url):
                try:
                    ig_res = await asyncio.to_thread(self._download_instagram, url, temp_dir)
                    if ig_res:
                        return ig_res
                except Exception as e:
                    logger.warning(f"Instagram engine error, trying fallback: {e}")

            # 3. Main yt-dlp download (handles YouTube, Instagram, TikTok, Reddit, Pinterest, Facebook, etc.)
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
                attempts.append({"use_cookies": False, "clients": ["ios", "android"]})

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
                    if is_youtube and att_idx < len(attempts) - 1:
                        logger.warning("Audio download attempt failed (%s), retrying without cookies...", e)
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
