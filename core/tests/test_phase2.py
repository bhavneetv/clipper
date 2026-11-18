import json
from decimal import Decimal
from unittest.mock import MagicMock, patch
from django.test import Client, TestCase
from core.jobs import run_watcher
from core.models import ActivityLog, Video
from core.video_probe import probe_video


class Phase2StorageIngestionTests(TestCase):
    def setUp(self):
        self.client = Client()
        session = self.client.session
        session["is_authenticated"] = True
        session.save()

    def test_probe_valid_vertical_video(self):
        """Mock ffprobe returning valid 1080x1920 vertical H.264/AAC MP4 video under 90s."""
        fake_ffprobe_output = {
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": "45.50",
                "size": "10485760",
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "duration": "45.50",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                },
            ],
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_ffprobe_output), stderr="")
            valid, reason, meta = probe_video("https://example.com/test.mp4", file_size_bytes=10485760)
            self.assertTrue(valid)
            self.assertIsNone(reason)
            self.assertEqual(meta["width"], 1080)
            self.assertEqual(meta["height"], 1920)
            self.assertEqual(meta["duration_s"], Decimal("45.50"))

    def test_probe_horizontal_video_rejected(self):
        """Horizontal video (width > height) must be rejected with invalid reason."""
        fake_ffprobe_output = {
            "format": {"format_name": "mp4", "duration": "30.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_ffprobe_output), stderr="")
            valid, reason, meta = probe_video("https://example.com/horizontal.mp4")
            self.assertFalse(valid)
            self.assertIn("not vertical", reason)

    def test_probe_exceeds_duration_limit(self):
        """Duration over 90 seconds must be rejected."""
        fake_ffprobe_output = {
            "format": {"format_name": "mp4", "duration": "95.2"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_ffprobe_output), stderr="")
            valid, reason, meta = probe_video("https://example.com/long.mp4")
            self.assertFalse(valid)
            self.assertIn("exceeds 90 seconds", reason)

    def test_probe_file_size_exceeded(self):
        """File size > 50MB must be rejected before ffprobe."""
        valid, reason, meta = probe_video("https://example.com/huge.mp4", file_size_bytes=60 * 1024 * 1024)
        self.assertFalse(valid)
        self.assertIn("exceeds 50 MB limit", reason)

    @patch("core.jobs.list_inbox_videos")
    @patch("core.jobs.probe_video")
    def test_watcher_ingestion_and_deduplication(self, mock_probe, mock_list):
        """
        Watcher processes 3 files:
        - 2 valid -> queued
        - 1 invalid (e.g. horizontal) -> invalid with reason
        - Repeated scan -> no duplicates
        - Duplicate hash -> marked invalid duplicate
        """
        mock_list.return_value = [
            {"name": "vid1.mp4", "key": "inbox/vid1.mp4", "size": 5000000, "etag": "etag1"},
            {"name": "vid2.mp4", "key": "inbox/vid2.mp4", "size": 6000000, "etag": "etag2"},
            {"name": "vid3_bad.mp4", "key": "inbox/vid3_bad.mp4", "size": 4000000, "etag": "etag3"},
        ]

        def probe_side_effect(url, file_size_bytes=0, *args, **kwargs):
            if "vid3_bad" in url:
                return False, "Video is not vertical: height (720) must be greater than width (1280).", {
                    "width": 1280, "height": 720, "duration_s": Decimal("20.0"), "size_bytes": file_size_bytes
                }
            return True, None, {
                "width": 1080, "height": 1920, "duration_s": Decimal("30.0"), "size_bytes": file_size_bytes
            }

        mock_probe.side_effect = probe_side_effect

        # First run
        run_watcher()

        self.assertEqual(Video.objects.count(), 3)
        v1 = Video.objects.get(file_key="inbox/vid1.mp4")
        self.assertEqual(v1.status, "queued")
        self.assertEqual(v1.queue_position, 1)

        v2 = Video.objects.get(file_key="inbox/vid2.mp4")
        self.assertEqual(v2.status, "queued")
        self.assertEqual(v2.queue_position, 2)

        v3 = Video.objects.get(file_key="inbox/vid3_bad.mp4")
        self.assertEqual(v3.status, "invalid")
        self.assertIn("not vertical", v3.invalid_reason)

        # Repeated run: no duplicates created
        run_watcher()
        self.assertEqual(Video.objects.count(), 3)

        # Test duplicate hash detection
        mock_list.return_value = [
            {"name": "vid4_dup.mp4", "key": "inbox/vid4_dup.mp4", "size": 5000000, "etag": "etag1"},
        ]
        run_watcher()
        self.assertEqual(Video.objects.count(), 4)
        v4 = Video.objects.get(file_key="inbox/vid4_dup.mp4")
        self.assertEqual(v4.status, "invalid")
        self.assertEqual(v4.invalid_reason, "duplicate")

    def test_videos_list_view_and_filtering(self):
        """GET /videos returns HTML table, supports filtering and search."""
        Video.objects.create(
            file_key="inbox/sample.mp4",
            file_name="sample.mp4",
            public_url="https://example.com/sample.mp4",
            yt_title="Super Amazing Short",
            status="queued",
        )
        res = self.client.get("/videos")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Super Amazing Short", res.content.decode("utf-8"))

        # HTMX partial
        res_htmx = self.client.get("/videos?status=queued", HTTP_HX_REQUEST="true")
        self.assertEqual(res_htmx.status_code, 200)
        self.assertIn("Super Amazing Short", res_htmx.content.decode("utf-8"))

        # Search filter
        res_empty = self.client.get("/videos?q=nonexistent", HTTP_HX_REQUEST="true")
        self.assertNotIn("Super Amazing Short", res_empty.content.decode("utf-8"))
        self.assertIn("No videos found", res_empty.content.decode("utf-8"))
