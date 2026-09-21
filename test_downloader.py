import unittest
from pathlib import Path
from downloader import (
    find_urls,
    detect_platform,
    is_pinterest_url,
    is_twitter_url,
    render_progress_bar,
    MediaDownloader,
    FFMPEG_EXE,
    VIDEO_EXTS,
    IMAGE_EXTS,
)
import config


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

    def test_downloader_init(self):
        dl = MediaDownloader()
        self.assertTrue(dl.download_dir.exists())


if __name__ == "__main__":
    unittest.main()
