import unittest
from pathlib import Path
from downloader import (
    find_urls,
    detect_platform,
    is_pinterest_url,
    resolve_pinterest_url,
    is_twitter_url,
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
        self.assertEqual(detect_platform("https://youtu.be/abc123"), "YouTube")
        self.assertEqual(detect_platform("https://x.com/user/status/123"), "X (Twitter)")
        self.assertEqual(detect_platform("https://www.tiktok.com/@user/video/123"), "TikTok")
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

    def test_user_modes(self):
        uid = 999999
        # Default mode is instant
        self.assertEqual(config.get_user_mode(uid), "instant")
        config.set_user_mode(uid, "picker")
        self.assertEqual(config.get_user_mode(uid), "picker")
        config.set_user_mode(uid, "instant")
        self.assertEqual(config.get_user_mode(uid), "instant")

    def test_admin_check(self):
        admin_user = DummyTelegramUser(id=12345, username="RahilAnw4r")
        self.assertTrue(config.is_admin(admin_user))

        normal_user = DummyTelegramUser(id=67890, username="regular_joe")
        self.assertFalse(config.is_admin(normal_user))

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


if __name__ == "__main__":
    unittest.main()

