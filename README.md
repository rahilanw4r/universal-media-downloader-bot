# 📥 All-in-One Social Media Downloader Telegram Bot

An instant, zero-configuration Telegram Bot that downloads videos, reels, posts, and audio from all major social media platforms and delivers them directly into Telegram.

---

## 🌟 Supported Platforms

- 📸 **Instagram:** Reels, Posts, Carousels, Stories
- 🎥 **YouTube:** Shorts, Full Videos, Music/MP3 extraction
- 🐦 **X (Twitter):** Videos, Clips & GIFs
- 🎵 **TikTok:** Watermark-free videos & audios
- 📌 **Pinterest:** Video pins & images
- 🤖 **Reddit & Facebook:** Videos & clips
- 🌐 **1000+ other websites:** Powered by `yt-dlp`

---

## ✨ Advanced Features

1. **🎛️ Dynamic Resolution Picker:** Inspects available resolutions and shows interactive buttons:
   - `[ 💎 1080p FHD ]`
   - `[ 📺 720p HD ]`
   - `[ 📱 480p SD ]`
   - `[ ⚡ 360p Data Saver ]`
2. **📊 Live Animated Progress Bar:** Real-time updates right on the message:
   ```text
   ⏳ Downloading from YouTube...
   [████████░░░░] 67%
   ⚡ Speed: 5.4 MB/s | Size: 16.2 MB / 24.1 MB
   ⏱️ ETA: 1s
   ```
3. **⚡ Instant Auto-Download Mode (`/settings`):** Toggle between Interactive Menu (choose resolution) and Instant Mode (zero-click auto delivery).
4. **👥 Group Chat Support:** Add the bot to any Telegram group chat—it automatically detects links and replies with playable videos directly in the group!
5. **🎵 Enhanced MP3 with Album Art & ID3 Tags:** Converts audio to 192k MP3 with official video cover art displayed in Telegram's built-in player.

---

## 🚀 Quick Start

### 1. Configuration
Open [.env](file:///m:/Projects/Pin/.env) and ensure your bot token is set:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
```

### 2. Start the Bot
Run:
```bash
python bot.py
```

---

## 📱 How to Use
1. Open [@UniversalMediaSaverBot](https://t.me/UniversalMediaSaverBot) on Telegram.
2. Tap `/start`.
3. Paste any video, reel, or post link into the chat.
4. Pick your desired quality (1080p, 720p, 480p) or MP3.
5. Send `/quality` or `/settings` to switch to Fast Instant mode anytime!
