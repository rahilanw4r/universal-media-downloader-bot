import asyncio
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
                attempts.append({"use_cookies": False, "clients": None})
                attempts.append({"use_cookies": False, "clients": ["web", "mweb"]})
                attempts.append({"use_cookies": False, "clients": ["tv_embedded", "android", "ios"]})

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
            "-preset", "ultrafast",
            "-threads", "0",
            "-c:a", "aac",
            "-b:a", f"{audio_bitrate}",
            "-movflags", "+faststart",
            str(output_path),
        ]

        try:
            logger.info("Compressing video (%s) to fit 50MB (bitrate: %d bps)...", input_path.name, video_bitrate)
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120)
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
