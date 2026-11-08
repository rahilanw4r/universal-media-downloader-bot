import unittest
from pathlib import Path
from downloader import (
    find_urls,
    detect_platform,
    is_pinterest_url,
    resolve_pinterest_url,
    is_twitter_url,
    is_instagram_url,
    is_tiktok_url,
    is_terabox_url,
    extract_terabox_surl,
    is_diskwala_url,
    extract_diskwala_id,
    is_reddit_url,
    resolve_reddit_url,
    extract_v_reddit_id,
    extract_reddit_video_id,
    render_progress_bar,
    MediaDownloader,
    FFMPEG_EXE,
    VIDEO_EXTS,
    IMAGE_EXTS,
)
import config
import analytics


class DummyTelegramUser:
    def __init__(self, id: int, username: str = "", first_name: str = ""):
        self.id = id
        self.username = username
        self.first_name = first_name


class TestDownloader(unittest.TestCase):

    def test_find_urls(self):
        text = "Check out this reel https://www.instagram.com/reel/C3abc123/ and this yt https://youtu.be/dQw4w9WgXcQ"
        urls = find_urls(text)
        self.assertEqual(len(urls), 2)
        self.assertIn("https://www.instagram.com/reel/C3abc123/", urls)
        self.assertIn("https://youtu.be/dQw4w9WgXcQ", urls)

    def test_detect_platform(self):
        self.assertEqual(detect_platform("https://www.instagram.com/reel/abc123/"), "Instagram")
        self.assertEqual(detect_platform("https://www.instagram.com/p/abc123/"), "Instagram")
        self.assertEqual(detect_platform("https://youtu.be/abc123"), "YouTube")
        self.assertEqual(detect_platform("https://x.com/user/status/123"), "X (Twitter)")
        self.assertEqual(detect_platform("https://twitter.com/user/status/123"), "X (Twitter)")
        self.assertEqual(detect_platform("https://www.tiktok.com/@user/video/123"), "TikTok")
        self.assertEqual(detect_platform("https://terabox.com/s/1abc123"), "TeraBox")
        self.assertEqual(detect_platform("https://teraboxapp.com/s/1abc123"), "TeraBox")
        self.assertEqual(detect_platform("https://1024tera.com/s/1xyz987"), "TeraBox")
        self.assertEqual(detect_platform("https://diskwala.com/app/66fd54e2"), "DiskWala")
        self.assertEqual(detect_platform("https://diskwala.fun/s/12345"), "DiskWala")
        self.assertEqual(detect_platform("https://pin.it/abc123"), "Pinterest")
        self.assertEqual(detect_platform("https://www.reddit.com/r/videos/comments/123/"), "Reddit")
        self.assertEqual(detect_platform("https://example.com/video.mp4"), "Web Video")

    def test_platform_helpers(self):
        self.assertTrue(is_pinterest_url("https://www.pinterest.com/pin/123456/"))
        self.assertTrue(is_pinterest_url("https://pin.it/abc1234"))
        self.assertFalse(is_pinterest_url("https://youtube.com/watch?v=123"))
        self.assertEqual(
            resolve_pinterest_url("https://www.pinterest.com/pin/123456/"),
            "https://www.pinterest.com/pin/123456/",
        )

        self.assertTrue(is_twitter_url("https://x.com/NASA/status/1783547844005695627"))
        self.assertTrue(is_twitter_url("https://twitter.com/user/status/123"))
        self.assertFalse(is_twitter_url("https://instagram.com/p/123"))
        self.assertFalse(is_twitter_url("https://terabox.com/s/123456"))
        self.assertFalse(is_twitter_url("https://diskwala.com/app/123456"))

        self.assertTrue(is_instagram_url("https://www.instagram.com/p/C-123456/"))
        self.assertTrue(is_instagram_url("https://instagram.com/reel/C-123456/"))
        self.assertTrue(is_instagram_url("https://instagr.am/p/C-123456/"))
        self.assertFalse(is_instagram_url("https://x.com/user/status/123"))

        self.assertTrue(is_tiktok_url("https://www.tiktok.com/@user/video/123456"))
        self.assertTrue(is_tiktok_url("https://vm.tiktok.com/ZM12345/"))
        self.assertTrue(is_tiktok_url("https://vt.tiktok.com/ZS12345/"))
        self.assertFalse(is_tiktok_url("https://youtube.com/watch?v=123"))

    def test_diskwala_helpers(self):
        self.assertTrue(is_diskwala_url("https://diskwala.com/app/66fd54e2abc"))
        self.assertTrue(is_diskwala_url("https://www.diskwala.com/app/66fd54e2abc"))
        self.assertTrue(is_diskwala_url("https://diskwala.fun/s/123456"))
        self.assertTrue(is_diskwala_url("https://diskwala.com/c/mycode"))
        self.assertFalse(is_diskwala_url("https://youtube.com/watch?v=123"))
        self.assertFalse(is_diskwala_url("https://terabox.com/s/123"))

        self.assertEqual(extract_diskwala_id("https://diskwala.com/app/66fd54e2abc"), "66fd54e2abc")
        self.assertEqual(extract_diskwala_id("https://diskwala.fun/s/myfile123"), "myfile123")
        self.assertEqual(extract_diskwala_id("https://diskwala.com/c/sharecode"), "sharecode")
        self.assertEqual(extract_diskwala_id("https://diskwala.com/file?id=target_id"), "target_id")

    def test_terabox_helpers(self):
        self.assertTrue(is_terabox_url("https://teraboxapp.com/s/1RCBLl4RBXh446ZKoHgHZJt_Q"))
        self.assertTrue(is_terabox_url("https://www.1024tera.com/sharing/link?surl=RCBLl4RBXh446ZKoHgHZJt_Q"))
        self.assertTrue(is_terabox_url("https://terabox.com/s/1abcdef"))
        self.assertFalse(is_terabox_url("https://youtube.com/watch?v=123"))

        self.assertEqual(extract_terabox_surl("https://teraboxapp.com/s/1RCBLl4RBXh446ZKoHgHZJt_Q"), "RCBLl4RBXh446ZKoHgHZJt_Q")
        self.assertEqual(extract_terabox_surl("https://1024tera.com/sharing/link?surl=TEST_KEY_123"), "TEST_KEY_123")

    def test_media_extensions(self):
        self.assertIn(".mp4", VIDEO_EXTS)
        self.assertIn(".png", IMAGE_EXTS)
        self.assertIn(".jpg", IMAGE_EXTS)
        self.assertIn(".webp", IMAGE_EXTS)

    def test_render_progress_bar(self):
        bar_0 = render_progress_bar(0, width=10)
        self.assertEqual(bar_0, "[░░░░░░░░░░] 0%")

        bar_50 = render_progress_bar(50, width=10)
        self.assertEqual(bar_50, "[█████░░░░░] 50%")

        bar_100 = render_progress_bar(100, width=10)
        self.assertEqual(bar_100, "[██████████] 100%")

    def test_user_auth(self):
        # By default when whitelist is empty, any user is allowed
        self.assertTrue(config.is_user_allowed(12345678))

    def test_ffmpeg_resolved(self):
        self.assertTrue(bool(FFMPEG_EXE), "FFmpeg binary should be resolved")

    def test_admin_config(self):
        self.assertEqual(config.ADMIN_USERNAME, "@RahilAnw4r")
        self.assertIn("RahilAnw4r", config.ADMIN_LINK)

    def test_admin_check(self):
        admin_user = DummyTelegramUser(id=12345, username="RahilAnw4r")
        self.assertTrue(config.is_admin(admin_user))

        normal_user = DummyTelegramUser(id=67890, username="regular_joe")
        self.assertFalse(config.is_admin(normal_user))

        # Whitelisted users should NOT have admin privileges
        orig_whitelist = list(config.ALLOWED_TELEGRAM_USER_IDS)
        try:
            config.ALLOWED_TELEGRAM_USER_IDS = [67890]
            self.assertFalse(config.is_admin(normal_user))
        finally:
            config.ALLOWED_TELEGRAM_USER_IDS = orig_whitelist

    def test_analytics(self):
        test_uid = 777888999
        analytics.track_user(test_uid, "tester_bot", "Tester")
        self.assertIn(test_uid, analytics.get_all_user_ids())

        analytics.track_download(test_uid, "YouTube", "video")
        analytics.track_download(test_uid, "Instagram", "photo")

        summary = analytics.get_analytics_summary()
        self.assertGreaterEqual(summary["total_users"], 1)
        self.assertGreaterEqual(summary["total_downloads"], 2)

        # Check dashboard generators return non-empty strings
        admin_dash = analytics.format_admin_dashboard()
        self.assertIn("Bot Analytics", admin_dash)
        self.assertIn("YouTube", admin_dash)

        pub_dash = analytics.format_public_dashboard()
        self.assertIn("Universal Media Downloader", pub_dash)

        uptime_str = analytics.format_uptime()
        self.assertTrue(bool(uptime_str))
        self.assertTrue(any(unit in uptime_str for unit in ("s", "m", "h", "d")))

    def test_cookies_management(self):
        # Test cleaning raw cookie content
        raw_cookie = ".youtube.com\tTRUE\t/\tTRUE\t1799999999\tLOGIN_INFO\tabc123"
        cleaned = config._clean_cookie_content(raw_cookie)
        self.assertTrue(cleaned.startswith("# Netscape HTTP Cookie File"))
        self.assertIn("LOGIN_INFO", cleaned)

        # Test base64 cookie normalization
        import base64
        b64 = base64.b64encode(raw_cookie.encode("utf-8")).decode("utf-8")
        cleaned_b64 = config._clean_cookie_content(b64)
        self.assertIn("LOGIN_INFO", cleaned_b64)

        # Test JSON cookie normalization (EditThisCookie format)
        import json
        json_cookies = json.dumps([
            {"domain": ".youtube.com", "name": "LOGIN_INFO", "value": "json_token", "path": "/", "secure": True},
            {"domain": ".instagram.com", "name": "sessionid", "value": "ig_sess", "path": "/", "secure": True},
        ])
        cleaned_json = config._clean_cookie_content(json_cookies)
        self.assertTrue(cleaned_json.startswith("# Netscape HTTP Cookie File"))
        self.assertIn("json_token", cleaned_json)
        self.assertIn("ig_sess", cleaned_json)

        # Test space-to-tab normalization
        space_cookie = ".youtube.com   TRUE   /   TRUE   1799999999   LOGIN_INFO   space123"
        cleaned_space = config._clean_cookie_content(space_cookie)
        self.assertIn("\tLOGIN_INFO\tspace123", cleaned_space)

        # Test raw header format normalization (key=val; key2=val2)
        header_cookie = "LOGIN_INFO=hdr123; sessionid=ig456; ndus=tb789"
        cleaned_header = config._clean_cookie_content(header_cookie)
        self.assertIn("hdr123", cleaned_header)
        self.assertIn("ig456", cleaned_header)
        self.assertIn("tb789", cleaned_header)

        # Test cookie file validation helper
        d = MediaDownloader()
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            valid_file = Path(td) / "valid.txt"
            valid_file.write_text(cleaned)
            self.assertTrue(d._is_valid_cookie_file(valid_file))

            bad_file = Path(td) / "bad.txt"
            bad_file.write_text("random junk that is not cookies")
            self.assertFalse(d._is_valid_cookie_file(bad_file))

        # Test cookie status diagnostics
        status = config.get_cookies_status()
        self.assertIn("loaded", status)
        self.assertIn("youtube_cookies", status)
        self.assertIn("instagram_cookies", status)
        self.assertIn("terabox_cookies", status)

    def test_force_sub_channel_config(self):
        self.assertTrue(hasattr(config, "FORCE_SUB_CHANNEL"))
        self.assertEqual(config.FORCE_SUB_CHANNEL, "@Bootscreen")

    def test_bot_command_handlers(self):
        try:
            import bot
            self.assertTrue(callable(bot.ping_command))
            self.assertTrue(callable(bot.clean_command))
            self.assertTrue(callable(bot.stop_command))
            self.assertTrue(callable(bot.post_init))
        except ModuleNotFoundError as e:
            if "telegram" in str(e):
                self.skipTest("python-telegram-bot not installed in test environment")
            raise

    def test_sanitize_image_file(self):
        from downloader import sanitize_image_file
        import tempfile
        from PIL import Image

        with tempfile.TemporaryDirectory() as td:
            # Create a simple valid image
            img_path = Path(td) / "test.png"
            img = Image.new("RGB", (100, 100), color="blue")
            img.save(img_path, format="PNG")

            sanitized = sanitize_image_file(img_path)
            self.assertIsNotNone(sanitized)
            self.assertTrue(sanitized.exists())
            self.assertEqual(sanitized.suffix.lower(), ".jpg")

    def test_instagram_oembed_parsing(self):
        from unittest.mock import patch, MagicMock
        import tempfile
        from downloader import MediaDownloader
        from PIL import Image

        d = MediaDownloader()
        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            # Create a mock valid image payload
            sample_img = temp_path / "sample.jpg"
            Image.new("RGB", (50, 50), color="green").save(sample_img, format="JPEG")
            img_bytes = sample_img.read_bytes()

            mock_oembed_resp = MagicMock()
            mock_oembed_resp.status_code = 200
            mock_oembed_resp.json.return_value = {
                "title": "Beautiful Sunset in Hawaii",
                "author_name": "traveler_jane",
                "thumbnail_url": "https://instagram.example.com/sunset.jpg",
            }

            mock_img_resp = MagicMock()
            mock_img_resp.status_code = 200
            mock_img_resp.content = img_bytes

            def mock_requests_get(url, *args, **kwargs):
                if "oembed" in url:
                    return mock_oembed_resp
                return mock_img_resp

            with patch("downloader.instaloader", None):
                with patch("downloader.requests.get", side_effect=mock_requests_get):
                    with patch.object(d, "_download_gallery_dl", return_value=None):
                        res = d._download_instagram("https://www.instagram.com/p/TEST12345/", temp_path)
                        self.assertIsNotNone(res)
                        self.assertEqual(res["type"], "photo")
                        self.assertEqual(res["title"], "Beautiful Sunset in Hawaii")
                        self.assertEqual(res["uploader"], "traveler_jane")
                        self.assertEqual(len(res["file_paths"]), 1)
                        self.assertTrue(res["file_path"].exists())

    def test_api_server_info(self):
        info = config.get_api_server_info()
        self.assertIn("version", info)
        self.assertEqual(info["version"], config.BOT_VERSION)
        self.assertIn("is_local", info)
        self.assertIn("base_url", info)
        self.assertIn("max_filesize_mb", info)
        self.assertIn("auto_split", info)
        self.assertIn("compression_enabled", info)
        self.assertFalse(info["compression_enabled"])
        self.assertGreaterEqual(info["max_filesize_mb"], 50.0)

    def test_split_video_under_threshold(self):
        from downloader import MediaDownloader
        import tempfile
        d = MediaDownloader()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tiny.mp4"
            p.write_bytes(b"dummy video content")
            parts = d._split_video_into_parts(p, Path(td), target_part_size_mb=50.0)
            self.assertEqual(parts, [p])

    def test_promo_channels_config(self):
        self.assertTrue(config.PROMO_CHANNELS_ENABLED)
        self.assertEqual(config.MOD_APK_CHANNEL, "ModRepository")
        self.assertEqual(config.WALLPAPER_CHANNEL, "WallpapersRepository")
        self.assertEqual(config.MOD_APK_URL, "https://t.me/ModRepository")
        self.assertEqual(config.WALLPAPER_URL, "https://t.me/WallpapersRepository")

    def test_promo_helpers(self):
        try:
            import bot
            footer = bot.get_promo_caption_footer()
            self.assertIn("@ModRepository", footer)
            self.assertIn("@WallpapersRepository", footer)

            kb = bot.get_media_post_keyboard(vid_session_id="test1234")
            self.assertIsNotNone(kb)
            # Row 1 is MP3 button, Row 2 is promo channels
            self.assertEqual(len(kb.inline_keyboard), 2)
            self.assertEqual(kb.inline_keyboard[0][0].callback_data, "dl:aud:test1234")
            self.assertEqual(kb.inline_keyboard[1][0].url, "https://t.me/ModRepository")
            self.assertEqual(kb.inline_keyboard[1][1].url, "https://t.me/WallpapersRepository")

            # Test without session id (for photo/audio)
            kb_no_vid = bot.get_media_post_keyboard()
            self.assertIsNotNone(kb_no_vid)
            self.assertEqual(len(kb_no_vid.inline_keyboard), 1)
            self.assertEqual(kb_no_vid.inline_keyboard[0][0].url, "https://t.me/ModRepository")
            self.assertEqual(kb_no_vid.inline_keyboard[0][1].url, "https://t.me/WallpapersRepository")
        except ModuleNotFoundError as e:
            if "telegram" in str(e):
                self.skipTest("python-telegram-bot not installed in test environment")
            raise

    def test_reddit_helpers(self):
        self.assertTrue(is_reddit_url("https://www.reddit.com/r/funny/comments/12345/title/"))
        self.assertTrue(is_reddit_url("https://reddit.com/r/videos/comments/abc/"))
        self.assertTrue(is_reddit_url("https://redd.it/12345"))
        self.assertTrue(is_reddit_url("https://v.redd.it/zv89llsvexdz"))
        self.assertTrue(is_reddit_url("https://i.redd.it/sample_pic.jpg"))
        self.assertTrue(is_reddit_url("https://vxreddit.com/comments/12345"))
        self.assertFalse(is_reddit_url("https://youtube.com/watch?v=123"))
        self.assertFalse(is_reddit_url("https://instagram.com/p/123"))
        self.assertFalse(is_reddit_url("https://twitter.com/user/status/123"))

        self.assertEqual(extract_reddit_video_id("https://v.redd.it/zv89llsvexdz"), "zv89llsvexdz")
        self.assertEqual(extract_reddit_video_id("https://v.redd.it/zv89llsvexdz/HLSPlaylist.m3u8"), "zv89llsvexdz")
        self.assertEqual(extract_reddit_video_id("https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/"), "6rrwyj")
        self.assertEqual(extract_reddit_video_id("https://redd.it/6rrwyj"), "6rrwyj")

        # extract_v_reddit_id checks
        self.assertEqual(extract_v_reddit_id("https://v.redd.it/zv89llsvexdz"), "zv89llsvexdz")
        self.assertEqual(extract_v_reddit_id("https://v.redd.it/zv89llsvexdz/HLSPlaylist.m3u8"), "zv89llsvexdz")
        self.assertIsNone(extract_v_reddit_id("https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/"))
        self.assertIsNone(extract_v_reddit_id("https://redd.it/6rrwyj"))

        # resolve_reddit_url checks
        self.assertEqual(resolve_reddit_url("https://www.reddit.com/r/funny/comments/12345/title/"), "https://www.reddit.com/r/funny/comments/12345/title/")
        from unittest.mock import patch, MagicMock
        with patch("requests.head") as mock_head:
            mock_resp = MagicMock()
            mock_resp.status_code = 307
            mock_resp.headers = {"Location": "https://www.reddit.com/r/memes/comments/xyz123/canonical/"}
            mock_head.return_value = mock_resp
            resolved = resolve_reddit_url("https://www.reddit.com/r/memes/s/abc123xyz")
            self.assertEqual(resolved, "https://www.reddit.com/r/memes/comments/xyz123/canonical/")

    def test_reddit_photo_download_mock(self):
        from unittest.mock import patch, MagicMock
        import tempfile
        from PIL import Image

        d = MediaDownloader()
        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            sample_img = temp_path / "sample.jpg"
            Image.new("RGB", (50, 50), color="blue").save(sample_img, format="JPEG")
            img_bytes = sample_img.read_bytes()

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_content.return_value = [img_bytes]
            mock_resp.content = img_bytes

            with patch("downloader.requests.get", return_value=mock_resp):
                res = d._download_reddit("https://i.redd.it/sample_img.jpg", temp_path)
                self.assertIsNotNone(res)
                self.assertEqual(res["type"], "photo")
                self.assertEqual(res["uploader"], "Reddit")
                self.assertTrue(Path(res["file_path"]).exists())

    def test_reddit_gallery_download_mock(self):
        from unittest.mock import patch, MagicMock
        import tempfile
        from PIL import Image

        d = MediaDownloader()
        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            sample_img = temp_path / "sample.jpg"
            Image.new("RGB", (50, 50), color="red").save(sample_img, format="JPEG")
            img_bytes = sample_img.read_bytes()

            html_content = """
            <html>
                <head>
                    <meta property="og:title" content="Awesome Art Gallery" />
                    <meta property="og:image" content="https://i.redd.it/img1.jpg" />
                    <meta property="og:image" content="https://i.redd.it/img2.jpg" />
                </head>
            </html>
            """

            mock_html_resp = MagicMock()
            mock_html_resp.status_code = 200
            mock_html_resp.text = html_content

            mock_img_resp = MagicMock()
            mock_img_resp.status_code = 200
            mock_img_resp.content = img_bytes

            def mock_get(url, *args, **kwargs):
                if "vxreddit.com" in url:
                    return mock_html_resp
                elif "rapidsave.com" in url:
                    r = MagicMock()
                    r.status_code = 404
                    return r
                return mock_img_resp

            with patch("downloader.requests.get", side_effect=mock_get):
                res = d._download_reddit("https://www.reddit.com/gallery/1gl333m", temp_path)
                self.assertIsNotNone(res)
                self.assertEqual(res["type"], "album")
                self.assertEqual(res["title"], "Awesome Art Gallery")
                self.assertEqual(len(res["file_paths"]), 2)
                for p in res["file_paths"]:
                    self.assertTrue(Path(p).exists())

    def test_reddit_video_download_mock(self):
        from unittest.mock import patch, MagicMock
        import tempfile

        d = MediaDownloader()
        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            mock_video = temp_path / "test_video.mp4"
            mock_video.write_bytes(b"dummy mp4 video bytes" * 100)

            mock_rs_html = """
            <html>
                <body>
                    <h2>Funniest Clip Ever</h2>
                    <a class="downloadbutton" href="https://sd.rapidsave.com/download.php?permalink=...&video_url=https://v.redd.it/zv89llsvexdz/DASH_2_4_M?source=fallback">Download</a>
                </body>
            </html>
            """
            mock_rs_resp = MagicMock()
            mock_rs_resp.status_code = 200
            mock_rs_resp.text = mock_rs_html

            def mock_ytdlp(url, t_dir, **kwargs):
                return {
                    "type": "video",
                    "file_path": mock_video,
                    "file_paths": [mock_video],
                    "dir_path": t_dir,
                    "title": "HLSPlaylist",
                    "uploader": "Reddit",
                    "duration": 12,
                    "thumbnail": None,
                    "thumb_file": None,
                    "filesize_mb": 0.5,
                }

            with patch("downloader.requests.get", return_value=mock_rs_resp):
                with patch.object(d, "_download_ytdlp", side_effect=mock_ytdlp):
                    res = d._download_reddit("https://www.reddit.com/r/videos/comments/6rrwyj/test/", temp_path)
                    self.assertIsNotNone(res)
                    self.assertEqual(res["type"], "video")
                    self.assertEqual(res["title"], "Funniest Clip Ever")
                    self.assertEqual(res["platform"], "Reddit")

    def test_reddit_audio_download_mock(self):
        from unittest.mock import patch, MagicMock
        import tempfile

        d = MediaDownloader()
        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            mock_mp3 = temp_path / "test_audio.mp3"
            mock_mp3.write_bytes(b"dummy mp3 audio bytes" * 100)

            mock_rs_html = """
            <html>
                <body>
                    <a class="downloadbutton" href="https://sd.rapidsave.com/download.php?permalink=...&video_url=https://v.redd.it/zv89llsvexdz/DASH_2_4_M?source=fallback">Download</a>
                </body>
            </html>
            """
            mock_rs_resp = MagicMock()
            mock_rs_resp.status_code = 200
            mock_rs_resp.text = mock_rs_html

            class MockYtdl:
                def __init__(self, opts):
                    self.opts = opts
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def extract_info(self, url, download=True):
                    return {"duration": 15}

            with patch("downloader.requests.get", return_value=mock_rs_resp):
                with patch("downloader.yt_dlp.YoutubeDL", MockYtdl):
                    res = d._download_reddit_audio("https://www.reddit.com/r/videos/comments/6rrwyj/test/", temp_path)
                    self.assertIsNotNone(res)
                    self.assertEqual(res["type"], "audio")
                    self.assertEqual(res["duration"], 15)
                    self.assertTrue(Path(res["file_path"]).exists())

    def test_reddit_domains_and_schemes(self):
        """Verify all Reddit domains, smart links, and schemes (http, https, www, bare) match."""
        test_urls = [
            "https://www.reddit.com/r/funny/comments/123/title/",
            "http://reddit.com/r/pics/comments/456/",
            "www.reddit.com/r/funny/comments/789/",
            "reddit.com/r/aww/comments/abc/",
            "https://redd.it/12345",
            "https://v.redd.it/xyz",
            "https://i.redd.it/pic.jpg",
            "https://preview.redd.it/preview.png",
            "https://reddit.app.link/smartlink",
            "https://www.reddit.com/r/memes/s/share123",
        ]
        for u in test_urls:
            self.assertTrue(is_reddit_url(u), f"Failed to match: {u}")
        self.assertFalse(is_reddit_url("https://youtube.com/watch?v=123"))
        self.assertFalse(is_reddit_url("https://instagram.com/p/123"))

    def test_reddit_gallery_mock(self):
        """Test gallery detection and download mock."""
        from unittest.mock import patch, MagicMock
        import tempfile
        import json
        from PIL import Image

        d = MediaDownloader()
        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            img1 = temp_path / "img1.jpg"
            img2 = temp_path / "img2.jpg"
            with Image.new("RGB", (400, 300), color="blue") as im:
                im.save(img1, "JPEG")
                im.save(img2, "JPEG")

            def mock_get(url, *args, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                if "gallery" in url:
                    resp.text = json.dumps(["https://preview.redd.it/a.jpg", "https://preview.redd.it/b.jpg"])
                elif "title" in url:
                    resp.text = "Cool 3D Gallery"
                elif "preview.redd.it" in url or "i.redd.it" in url:
                    resp.content = img1.read_bytes()
                else:
                    resp.text = "OK"
                return resp

            with patch("downloader.requests.get", side_effect=mock_get):
                res = d._download_reddit("https://www.reddit.com/r/blender/comments/1basx0i/test/", temp_path)
                self.assertIsNotNone(res)
                self.assertEqual(res["type"], "album")
                self.assertEqual(res["title"], "Cool 3D Gallery")
                self.assertEqual(len(res["file_paths"]), 2)


if __name__ == "__main__":
    unittest.main()

