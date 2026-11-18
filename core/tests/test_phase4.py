from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import zoneinfo
from django.test import TestCase
from core.jobs import derive_overall_status, reset_stuck_uploads, run_uploader
from core.models import Account, ActivityLog, Video
from core.scheduling import set_fake_now
from core.settings_store import init_default_settings


class Phase4PlatformUploadTests(TestCase):
    def setUp(self):
        init_default_settings()
        self.t0 = datetime(2026, 11, 18, 12, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        set_fake_now(self.t0)

    def tearDown(self):
        set_fake_now(None)

    @patch("core.jobs.upload_video_to_youtube")
    @patch("core.jobs.publish_reel_to_instagram")
    def test_upload_success_and_overall_posted(self, mock_ig, mock_yt):
        """Mock successful upload to both YouTube and Instagram, verifying overall status becomes posted."""
        mock_yt.return_value = (True, None, {"video_id": "yt_123", "url": "https://youtube.com/shorts/yt_123"})
        mock_ig.return_value = (True, None, {"media_id": "ig_456", "permalink": "https://instagram.com/reel/ig_456/"})

        v = Video.objects.create(
            file_key="inbox/success.mp4",
            file_name="success.mp4",
            public_url="https://example.com/success.mp4",
            status="uploading",
            approved_at=self.t0,
            yt_status="pending",
            ig_status="pending",
        )

        # First uploader tick processes YouTube
        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.yt_status, "posted")
        self.assertEqual(v.yt_video_id, "yt_123")
        self.assertEqual(v.status, "uploading")  # Instagram still pending

        # Second uploader tick processes Instagram
        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.ig_status, "posted")
        self.assertEqual(v.ig_media_id, "ig_456")
        self.assertEqual(v.status, "posted")  # Both platforms completed
        self.assertIsNotNone(v.delete_after)
        self.assertEqual(v.delete_after, self.t0 + timedelta(days=7))

    @patch("core.jobs.upload_video_to_youtube")
    def test_retry_backoff_and_final_failure(self, mock_yt):
        """Failure with retries: backoff 5m, 30m, and final failure on 3rd attempt."""
        mock_yt.return_value = (False, "Network connection error", None)

        v = Video.objects.create(
            file_key="inbox/fail.mp4",
            file_name="fail.mp4",
            public_url="https://example.com/fail.mp4",
            status="uploading",
            approved_at=self.t0,
            yt_status="pending",
            ig_status="skipped",
        )

        # Attempt 1
        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.yt_attempts, 1)
        self.assertEqual(v.yt_status, "pending")
        self.assertEqual(v.yt_next_attempt_at, self.t0 + timedelta(minutes=5))

        # Advance clock 5 minutes -> Attempt 2
        set_fake_now(self.t0 + timedelta(minutes=5))
        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.yt_attempts, 2)
        self.assertEqual(v.yt_status, "pending")
        self.assertEqual(v.yt_next_attempt_at, self.t0 + timedelta(minutes=35))

        # Advance clock 30 minutes -> Attempt 3 (Final failure)
        set_fake_now(self.t0 + timedelta(minutes=35))
        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.yt_attempts, 3)
        self.assertEqual(v.yt_status, "failed")
        self.assertIn("Network connection error", v.yt_error)
        self.assertEqual(v.status, "failed")

    @patch("core.jobs.upload_video_to_youtube")
    def test_quota_error_defers_without_counting_attempt(self, mock_yt):
        """YouTube quota error defers upload without counting toward retry limit."""
        mock_yt.return_value = (False, "quotaExceeded", None)

        v = Video.objects.create(
            file_key="inbox/quota.mp4",
            file_name="quota.mp4",
            public_url="https://example.com/quota.mp4",
            status="uploading",
            approved_at=self.t0,
            yt_status="pending",
            ig_status="skipped",
        )

        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.yt_attempts, 0)  # Not incremented
        self.assertEqual(v.yt_status, "pending")
        self.assertGreaterEqual(v.yt_next_attempt_at, self.t0 + timedelta(hours=7))

    @patch("core.jobs.upload_video_to_youtube")
    @patch("core.jobs.publish_reel_to_instagram")
    def test_platform_independence(self, mock_ig, mock_yt):
        """YouTube failure does not block Instagram from succeeding."""
        mock_yt.return_value = (False, "Persistent API Error", None)
        mock_ig.return_value = (True, None, {"media_id": "ig_success", "permalink": "https://ig.com/reel/1"})

        v = Video.objects.create(
            file_key="inbox/indep.mp4",
            file_name="indep.mp4",
            public_url="https://example.com/indep.mp4",
            status="uploading",
            approved_at=self.t0,
            yt_status="pending",
            yt_attempts=2,  # next attempt will mark it failed
            ig_status="pending",
        )

        # Tick 1: YouTube attempt 3 fails
        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.yt_status, "failed")
        self.assertEqual(v.ig_status, "pending")
        self.assertEqual(v.status, "uploading")  # Instagram is still pending

        # Tick 2: Instagram succeeds
        run_uploader()
        v.refresh_from_db()
        self.assertEqual(v.ig_status, "posted")
        # Per PRD section 6: No platform pending/uploading, and at least one failed -> failed
        self.assertEqual(v.status, "failed")

    def test_restart_mid_upload_never_double_posts(self):
        """If yt_video_id is already present, uploader marks posted and does not re-upload."""
        v = Video.objects.create(
            file_key="inbox/restarted.mp4",
            file_name="restarted.mp4",
            public_url="https://example.com/restarted.mp4",
            status="uploading",
            approved_at=self.t0,
            yt_status="pending",
            yt_video_id="already_created_123",
            ig_status="skipped",
        )

        with patch("core.jobs.upload_video_to_youtube") as mock_yt:
            run_uploader()
            # Must NOT call YouTube API
            mock_yt.assert_not_called()
            v.refresh_from_db()
            self.assertEqual(v.yt_status, "posted")

    def test_simultaneous_claims_atomic(self):
        """Simultaneous claims on the same job allow only one worker to succeed."""
        v = Video.objects.create(
            file_key="inbox/claim.mp4",
            file_name="claim.mp4",
            public_url="https://example.com/claim.mp4",
            status="uploading",
            approved_at=self.t0,
            yt_status="pending",
        )

        claim1 = Video.objects.filter(id=v.id, yt_status="pending").update(yt_status="uploading")
        claim2 = Video.objects.filter(id=v.id, yt_status="pending").update(yt_status="uploading")

        self.assertEqual(claim1, 1)
        self.assertEqual(claim2, 0)
