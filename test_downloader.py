import unittest
from downloader import find_urls, detect_platform, render_progress_bar, MediaDownloader, FFMPEG_EXE
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

    def test_render_progress_bar(self):
        bar_0 = render_progress_bar(0, width=10)
        self.assertEqual(bar_0, "[░░░░░░░░░░] 0%")

        bar_50 = render_progress_bar(50, width=10)
        self.assertEqual(bar_50, "[█████░░░░░] 50%")

        bar_100 = render_progress_bar(100, width=10)
        self.assertEqual(bar_100, "[██████████] 100%")

    def test_user_settings(self):
        test_uid = 12345678
        config.set_user_setting(test_uid, "mode", "instant")
        self.assertEqual(config.get_user_setting(test_uid, "mode"), "instant")

        config.set_user_setting(test_uid, "mode", "interactive")
        self.assertEqual(config.get_user_setting(test_uid, "mode"), "interactive")

    def test_ffmpeg_resolved(self):
        self.assertTrue(bool(FFMPEG_EXE), "FFmpeg binary should be resolved")


if __name__ == "__main__":
    unittest.main()
