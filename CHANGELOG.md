# Changelog

All notable changes to this project are documented here.

## [2.6.0] - 2026-11-03

### Added
- **DiskWala Engine:** Full support for DiskWala links (`diskwala.com`, `diskwala.fun`, `/app/<id>`, `/s/<id>`, `/c/<code>`) with direct streaming, progress bars, and zero-loss auto-splitting.
- **Multi-Tier TeraBox Resolvers:** Added HTTP redirect resolution, dual `surl` candidate formats, and `diskwala.fun/api/resolve` upstream fallback.
- **DiskWala Configuration:** Added `DISKWALA_API_KEY` and `DISKWALA_API_URL` to `config.py` for optional private/third-party API extraction.

### Fixed
- **TeraBox "Downloading from X" Bug:** Resolved platform conflict where `"x.com"` matched `"terabox.com"` as a substring in `PLATFORM_DOMAINS` and `is_twitter_url()`.
- **Strict Domain Resolution:** Replaced naive substring URL checks across all platform matchers with strict parsed hostname (`netloc`) checks.

---

## [2.5.0] - 2026-11-03

### Added
- **Local Bot API (2 GB Uploads):** Added support for self-hosted `telegram-bot-api` to bypass Telegram's 50 MB limit and upload files up to 2,000 MB (2 GB).
- **Zero-Loss Video Auto-Splitting:** Added `_split_video_into_parts` using FFmpeg stream copy (`-c copy`) to seamlessly slice oversized videos with 100% original visual & audio quality preserved.
- **TeraBox / EBox Downloader:** Added full support for TeraBox and mirror domains (`terabox.com`, `1024tera.com`, `freeterabox.com`, etc.) with multi-tier link resolution.
- **Task Cancellation:** Added `/stop` and `/cancel` commands to abort running download tasks and clear pending quality picker menus.
- **Changelog Tracking:** Added `/changelog` command in-bot and `CHANGELOG.md` for version release tracking.
- **Deployment Blueprints:** Added native deployment support for Docker, Koyeb, and Render (`render.yaml`).

### Changed
- **Removed Default Video Compression:** Disabled lossy re-encoding by default; all videos and photos now deliver in 100% full original quality.
- **Centralized Versioning:** Added `BOT_VERSION` in `config.py` referenced across `/about` and diagnostics.
- **Dependency Upgrades:** Bumped minimum dependency constraints across `requirements.txt` (`python-telegram-bot>=21.0`, `yt-dlp>=2026.10.26`, etc.).

### Security
- **Sanitized Repository:** Stripped all personal developer credentials (`API_ID`, `API_HASH`) from git tracking; credentials now reside strictly inside local `.env`.

---

## [2.4.0] - 2026-11-02

### Added
- **Platform Cookie Diagnostics:** Added `/cookies` command and in-chat document handler to update `cookies.txt` without server restart.
- **Direct fxTwitter/vxTwitter API:** High-speed Twitter/X post, clip, and video extraction.

### Fixed
- **YouTube Extraction Failure:** Upgraded `yt-dlp` to `2026.10.26` and resolved `Failed to extract any player response` with multi-client fallback (`tv_embedded`, `android`, `ios`).
- **Instagram Carousel Delivery:** Fixed `image_process_failed` rendering error with sequential item fallback.

---

## [2.3.0] - 2026-10-30

### Added
- **Interactive Resolution Picker:** Added dynamic resolution buttons (`1080p`, `720p`, `480p`, `360p`) and one-click `/mode` toggle for instant downloads.
- **Live Animated Progress Bar:** Added real-time Telegram progress bar with speed, downloaded bytes, and ETA.
- **Studio MP3 Extractor:** Added `/mp3` and `/audio` command with embedded cover art.

---

## [2.0.0] - 2026-10-25

### Added
- **Initial Multi-Platform Downloader:** Core architecture supporting YouTube, Instagram, TikTok, Pinterest, Reddit, Facebook, and 1000+ web platforms via yt-dlp.
- **Admin & Analytics System:** Added `/stats` tracking, user whitelist filtering, and `/broadcast` announcements.

