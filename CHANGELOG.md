# Changelog

All notable changes to this project are documented here.

## [2.8.1] - 2026-11-18

### Added
- **Unauthenticated Instagram Download Engine (Reels, Videos, Photos, Carousels):**
  - Integrated SaveInsta and Cobalt API resolvers with direct Meta CDN JWT token extraction, bypassing Meta's unauthenticated login walls and empty media responses.
  - Added parallel asynchronous multi-slide carousel downloads using `ThreadPoolExecutor` and 512 KB streaming buffers, cutting album download time by over 5x.
  - Added Instagram logged-out embed `contextJSON` metadata scraper to extract authentic emoji post captions, author usernames, and video duration without requiring cookies.

### Fixed
- **Crystal-Clear HD Profile Picture Quality & Uncompressed Delivery:**
  - Resolved blurry low-resolution 150x150 thumbnail downloads on Instagram by integrating native `profile_pic.www.1080.C3` (1080x1080) original CDN avatar extraction via SaveInsta, Dumpor, and Greatfon.
  - Fixed duplicate `@handle (@handle)` formatting in profile captions, standardizing cleanly on `👤 @username`.
  - Fixed callback handling for `[ 📁 Full HD File ]` (`dl:doc:<id>` and `doc_<id>`), ensuring uncompressed original quality documents are delivered on click.
- **Local Bot API Offline Resilience:**
  - Added socket pre-flight connectivity check for `LOCAL_BOT_API_URL` to seamlessly fall back to Cloud Telegram API if the local server is offline without crashing.

## [2.8.0] - 2026-11-18

### Added
- **Full HD Profile Picture & Avatar Downloader:**
  - Automatically downloads full-resolution profile pictures and avatars across Instagram, TikTok, X (Twitter), GitHub, Telegram, YouTube, Pinterest, Reddit, and Threads.
  - Supports direct profile link sharing (e.g. `https://instagram.com/cristiano`, `https://github.com/torvalds`), dedicated commands (`/pfp`, `/profile`, `/avatar`), platform-prefix queries (`ig:cristiano`, `gh:torvalds`), and `@handle` inputs.
  - Displays user display name, handle, platform, bio description, avatar file size, `[ 🔗 Open Profile ]` URL button, and channel recommendation buttons.

### Fixed
- **Standardized Inline Buttons & Captions:**
  - Attached `[ 🎵 Extract MP3 Audio ]` and `[ 📱 Mod APKs | 🖼️ Wallpapers ]` consistently across all media download types (single videos, split video parts, carousels/albums, single photos, documents/archives, and MP3 audio).
  - Ensured multi-part auto-split video parts all carry action and channel buttons.
  - Standardized bullet metadata formatting across all caption fallbacks.

---

## [2.7.0] - 2026-11-17

### Added
- **Channel Recommendation Buttons:** Interactive inline keyboard buttons for Mod APKs (`@ModRepository`) and 4K Wallpapers (`@WallpapersRepository`) attached directly beneath media deliveries.
- **Reddit Video & Audio Extraction:** Improved Reddit URL parsing with direct `v.redd.it` extraction and automatic DASH audio+video stream merging, preventing article fallback on video posts.

### Changed
- **Professional Output Captions:** Fully redesigned media delivery captions across video, photo, album, document, article, and audio outputs to feature clean, modern styling (Title, Platform, Size, Duration).
- **Purged Quality Picker & Caption Clutter:** Completely eliminated resolution/quality picker prompts (always downloads full original quality) and removed channel text and bot usernames from captions.
- **Dependency Upgrades:** Upgraded `gallery-dl` to 1.32.20 and added `beautifulsoup4` to requirements.

### Security
- **Privacy Audit:** Confirmed complete data privacy — tokens, session files, databases, cookies, and local downloads are strictly ignored by `.gitignore` and omitted from commits.

---

## [2.6.0] - 2026-11-03

### Added
- **DiskWala Engine:** Full support for DiskWala links (`diskwala.com`, `diskwala.fun`, `/app/<id>`, `/s/<id>`, `/c/<code>`) with direct streaming, progress bars, and zero-loss auto-splitting.
- **Multi-Tier TeraBox Resolvers:** Added HTTP redirect resolution, dual `surl` candidate formats, and `diskwala.fun/api/resolve` upstream fallback.
- **DiskWala Configuration:** Added `DISKWALA_API_KEY` and `DISKWALA_API_URL` to `config.py` for optional private/third-party API extraction.

### Removed
- **Streamlined Interaction:** Removed redundant interaction menus and `/mode` command for direct, automatic media delivery.

### Fixed
- **TeraBox "Downloading from X" Bug:** Resolved platform conflict where `"x.com"` matched `"terabox.com"` as a substring in `PLATFORM_DOMAINS` and `is_twitter_url()`.
- **Strict Domain Resolution:** Replaced naive substring URL checks across all platform matchers with strict parsed hostname (`netloc`) checks.

---

## [2.5.0] - 2026-11-03

### Added
- **Local Bot API (2 GB Uploads):** Added support for self-hosted `telegram-bot-api` to bypass Telegram's 50 MB limit and upload files up to 2,000 MB (2 GB).
- **Video Auto-Splitting:** Added `_split_video_into_parts` using FFmpeg stream copy (`-c copy`) to seamlessly slice oversized videos without re-encoding.
- **TeraBox / EBox Downloader:** Added full support for TeraBox and mirror domains (`terabox.com`, `1024tera.com`, `freeterabox.com`, etc.) with multi-tier link resolution.
- **Task Cancellation:** Added `/stop` and `/cancel` commands to abort running download tasks and clear active sessions.
- **Changelog Tracking:** Added `/changelog` command in-bot and `CHANGELOG.md` for version release tracking.
- **Deployment Blueprints:** Added native deployment support for Docker, Koyeb, and Render (`render.yaml`).

### Changed
- **Direct Source Streams:** Disabled lossy re-encoding by default; media is delivered as direct source streams.
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
- **Format Selection Engine:** Added multi-format resolution parsing and direct download options.
- **Live Animated Progress Bar:** Added real-time Telegram progress bar with speed, downloaded bytes, and ETA.
- **Studio MP3 Extractor:** Added `/mp3` and `/audio` command with embedded cover art.

---

## [2.0.0] - 2026-10-25

### Added
- **Initial Multi-Platform Downloader:** Core architecture supporting YouTube, Instagram, TikTok, Pinterest, Reddit, Facebook, and 1000+ web platforms via yt-dlp.
- **Admin & Analytics System:** Added `/stats` tracking, user whitelist filtering, and `/broadcast` announcements.

