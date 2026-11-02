<div align="center">

# 📥 Universal Media Downloader Bot

### An open-source, ultra-fast Telegram bot for downloading media, full videos up to 2 GB, and extracting MP3 audio from all major platforms.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10 | 3.11 | 3.12](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram%20Bot%20API-v21%2B-blue?logo=telegram)](https://core.telegram.org/bots/api)
[![yt-dlp](https://img.shields.io/badge/Powered%20By-yt--dlp-red?logo=youtube)](https://github.com/yt-dlp/yt-dlp)
[![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![CI Tests](https://github.com/rahilanw4r/universal-media-downloader-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/rahilanw4r/universal-media-downloader-bot/actions)

[Live Bot Demo](https://t.me/UniversalMediaSaverBot) • [Report Bug](.github/ISSUE_TEMPLATE/bug_report.md) • [Request Feature](.github/ISSUE_TEMPLATE/feature_request.md)

</div>

---

## 🌟 Supported Platforms

| Platform | Supported Content | Highlights |
|---|---|---|
| 🎥 **YouTube** | Shorts, Full Videos, Live Recordings | 1080p, 720p, 480p, 360p, MP3 |
| 📸 **Instagram** | Reels, Posts, Carousels, Stories | Multi-item albums, HD audio |
| 📦 **TeraBox / EBox** | Shared files & videos (`terabox`, `1024tera`, `freeterabox`, etc.) | Direct link resolution & multi-tier fallback |
| 🐦 **X (Twitter)** | Videos, Clips, Animated GIFs | Best available stream via fxTwitter/vxTwitter |
| 🎵 **TikTok** | Videos & Sounds | Clean watermark-free MP4 |
| 📌 **Pinterest** | Video Pins & High-Res Clips | Clean direct video download |
| 🤖 **Reddit** | Videos with Audio | Auto-merges video and audio streams |
| 📘 **Facebook** | Reels & Public Watch Videos | Fast delivery |
| 🌐 **1000+ Others** | Vimeo, Dailymotion, Twitch Clips, etc. | Powered by `yt-dlp` |

---

## ✨ Key Features

- **🚀 2,000 MB (2 GB) Upload Capability:** Supports Telegram's official Local Bot API server (`telegram-bot-api`) to send videos and files up to **2 GB (2,000 MB)**.
- **✂️ Zero-Loss Video Auto-Splitting:** If a video exceeds the upload limit, it is automatically sliced into seamless parts (`Part 1/N`, `Part 2/N`) using FFmpeg stream copy (`-c copy`) in 1–2 seconds with **100% original visual & audio quality**.
- **🎛️ Dynamic Resolution Picker:** Inspects incoming video streams and provides interactive buttons:
  - `[ 💎 1080p FHD ]` `[ 📺 720p HD ]` `[ 📱 480p SD ]` `[ ⚡ 360p Data Saver ]`
- **⚡ Instant Zero-Click Mode:** Send `/mode` to switch to Instant Mode. Paste any link and receive the video immediately with **zero extra clicks**!
- **🛑 Instant Download Cancellation:** Cancel running download tasks or clear pending quality menus at any time with `/stop` or `/cancel`.
- **📊 Real-time Animated Progress Bar:** Live updates directly on the Telegram status message showing percentage, speed, downloaded size, and ETA:
  ```text
  ⏳ Downloading from YouTube...
  [████████░░░░] 67%
  ⚡ Speed: 5.4 MB/s | Size: 16.2 MB / 24.1 MB
  ⏱️ ETA: 1s
  ```
- **🎵 Studio MP3 Extraction:** Converts any video into a 192k MP3 audio file with the **official video thumbnail embedded as Album Cover Art** for Telegram's built-in music player.
- **📈 Built-in Analytics & Admin Dashboard:** Real-time user tracking and download statistics:
  - `/stats` (or `/analytics`): Total registered users, 24h & 7d active users, total downloads, and platform breakdown.
  - `/broadcast <message>`: Admin announcement broadcasting directly to all registered users.
  - `/cookies`: Diagnostic status for YouTube, Instagram, and TeraBox sessions, with in-chat document updates.
- **👥 Group Chat Ready:** Add the bot to any Telegram group chat with the 1-tap `[➕ Add Me to Your Group]` button in `/start`!

---

## 🚀 Deployment Options

### 1. Local Setup (Linux / macOS / Windows)

```bash
# Clone the repository
git clone https://github.com/rahilanw4r/universal-media-downloader-bot.git
cd universal-media-downloader-bot

# Install dependencies
pip install -r requirements.txt

# Configure Environment Variables
cp .env.example .env
nano .env  # Enter your TELEGRAM_BOT_TOKEN

# Run the Bot
python bot.py
```

---

### 2. Termux (Android Phone)

Run your bot 24/7 directly from your Android phone:

```bash
# Install packages
pkg update && pkg upgrade -y
pkg install -y python git ffmpeg clang libjpeg-turbo libpng

# Clone and setup
git clone https://github.com/rahilanw4r/universal-media-downloader-bot.git
cd universal-media-downloader-bot
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
nano .env  # Add your TELEGRAM_BOT_TOKEN

# Prevent Android from sleeping and launch
termux-wake-lock
python bot.py
```

---

### 3. Docker Deployment (With 2 GB Local Bot API)

Run both the bot and Telegram's Local Bot API Server with 1 command:

```bash
# Set your TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_BOT_TOKEN in .env
docker compose up -d
```

To view live logs:
```bash
docker compose logs -f
```

---

### 4. Deploy on Render

1. Create a new **Web Service** on [Render Dashboard](https://dashboard.render.com).
2. Connect your GitHub repository.
3. Configure settings:
   - **Environment:** `Python`
   - **Build Command:** `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
4. Add Environment Variable:
   - `TELEGRAM_BOT_TOKEN` = `your_telegram_bot_token`
5. Click **Deploy**! (The bot includes a built-in health server that automatically binds to `$PORT` to satisfy Render's health checks).

---

### 5. Deploy on Koyeb

1. Create a new service on [Koyeb](https://www.koyeb.com/).
2. Select **GitHub** and choose this repository.
3. Set Environment Variable `TELEGRAM_BOT_TOKEN`.
4. Deploy!

---

## 💬 Community & Support

Join [@BootScreenCommunity](https://t.me/BootScreenCommunity) for user discussion and support. Updates channel: [@Bootscreen](https://t.me/Bootscreen).

---

## ⌨️ Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome greeting, supported platforms, and status |
| `/mode` | Flip between *⚡ Instant Mode* and *🔘 Quality Picker* |
| `/mp3 <url>` (alias: `/audio`) | Extract MP3 audio with cover art |
| `/stop` (alias: `/cancel`) | Abort active download task or cancel quality picker |
| `/stats` (alias: `/analytics`) | View bot usage analytics & platform breakdown |
| `/cookies` | View platform authentication diagnostics & update cookies |
| `/broadcast <message>` | Admin-only broadcast message to registered users |
| `/contact` (alias: `/admin`) | Contact the bot developer & admin directly (@RahilAnw4r) |
| `/about` | Technical specs, engine versions, and architecture |
| `/help` | Complete user manual and instructions |

---

## 👑 Author & Admin Contact

- **Admin / Developer:** Rahil Anwar ([@RahilAnw4r](https://t.me/RahilAnw4r))
- **Telegram Support:** [t.me/RahilAnw4r](https://t.me/RahilAnw4r)
- **GitHub:** [@rahilanw4r](https://github.com/rahilanw4r)

Feel free to reach out for feature requests, issues, or feedback!

---

## 🧪 Running Tests

To run the automated unit test suite:
```bash
python3 -m unittest test_downloader.py
```

---

## 📄 License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

---

<div align="center">
⭐ Star this repository if you find it helpful!
</div>
