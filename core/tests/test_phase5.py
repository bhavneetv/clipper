from datetime import datetime, timedelta
import smtplib
from unittest.mock import MagicMock, patch
import zoneinfo
from django.test import TestCase, override_settings
from core.jobs import run_cleanup
from core.models import ActivityLog, Video
from core.notifier import notify_post_failure, notify_post_success
from core.scheduling import set_fake_now
from core.settings_store import init_default_settings, set_setting


@override_settings(SMTP_HOST="smtp.example.com", SMTP_USER="mailer@example.com")
class Phase5NotificationAndCleanupTests(TestCase):
    def setUp(self):
        init_default_settings()
        set_setting("notify_enabled", True)
        set_setting("notify_email", "admin@example.com")
        self.t0 = datetime(2026, 11, 18, 12, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        set_fake_now(self.t0)

    def tearDown(self):
        set_fake_now(None)

    @patch("smtplib.SMTP")
    def test_notification_sent_on_success_and_failure(self, mock_smtp):
        """Notification emails sent for success and failure when enabled."""
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance

        v = Video.objects.create(
            file_key="inbox/notif.mp4",
            file_name="notif.mp4",
            public_url="https://example.com/notif.mp4",
            yt_url="https://youtube.com/shorts/test1234",
            yt_status="posted",
        )

        # 1. Success notification
        notify_post_success(v, "youtube", async_send=False)
        instance.send_message.assert_called_once()
        sent_msg = instance.send_message.call_args[0][0]
        self.assertIn("[Shorts] Posted to YouTube", sent_msg["Subject"])
        self.assertEqual(sent_msg["To"], "admin@example.com")

        # 2. Disabled notification -> no email sent
        instance.reset_mock()
        set_setting("notify_enabled", False)
        notify_post_success(v, "youtube", async_send=False)
        instance.send_message.assert_not_called()

        # 3. Final failure notification
        set_setting("notify_enabled", True)
        notify_post_failure(v, "youtube", "Upload rejected by API", async_send=False)
        instance.send_message.assert_called_once()
        sent_fail = instance.send_message.call_args[0][0]
        self.assertIn("[Shorts] FAILED on YouTube", sent_fail["Subject"])

    @patch("smtplib.SMTP")
    def test_smtp_failure_does_not_break_flow(self, mock_smtp):
        """SMTP exceptions log email_failed and do not crash calling thread."""
        mock_smtp.side_effect = smtplib.SMTPException("Connection refused")
        v = Video.objects.create(file_key="inbox/err.mp4", file_name="err.mp4", public_url="u")
        # Should not raise exception
        notify_post_success(v, "youtube", async_send=False)
        log = ActivityLog.objects.filter(event="email_failed").first()
        self.assertIsNotNone(log)

    @patch("core.jobs.delete_file")
    def test_cleanup_deletes_only_eligible_posted_videos(self, mock_delete):
        """
        Cleanup deletes only files 7 days after successful post:
        - status='posted', delete_after <= now, keep=False
        Never deletes:
        - status='queued', 'scheduled', 'awaiting_approval', 'uploading', 'failed', 'invalid'
        - keep=True
        - delete_after in the future
        """
        mock_delete.return_value = True

        # 1. Eligible for cleanup
        v_eligible = Video.objects.create(
            file_key="inbox/v_eligible.mp4",
            file_name="v_eligible.mp4",
            public_url="u",
            status="posted",
            keep=False,
            delete_after=self.t0 - timedelta(hours=1),
        )

        # 2. Keep flag true
        v_keep = Video.objects.create(
            file_key="inbox/v_keep.mp4",
            file_name="v_keep.mp4",
            public_url="u",
            status="posted",
            keep=True,
            delete_after=self.t0 - timedelta(hours=1),
        )

        # 3. Future delete_after
        v_future = Video.objects.create(
            file_key="inbox/v_future.mp4",
            file_name="v_future.mp4",
            public_url="u",
            status="posted",
            keep=False,
            delete_after=self.t0 + timedelta(days=2),
        )

        # 4. Ineligible statuses
        v_queued = Video.objects.create(file_key="inbox/vq.mp4", file_name="vq.mp4", public_url="u", status="queued")
        v_sched = Video.objects.create(file_key="inbox/vs.mp4", file_name="vs.mp4", public_url="u", status="scheduled")
        v_await = Video.objects.create(file_key="inbox/va.mp4", file_name="va.mp4", public_url="u", status="awaiting_approval")
        v_upload = Video.objects.create(file_key="inbox/vu.mp4", file_name="vu.mp4", public_url="u", status="uploading")
        v_failed = Video.objects.create(file_key="inbox/vf.mp4", file_name="vf.mp4", public_url="u", status="failed")
        v_invalid = Video.objects.create(file_key="inbox/vi.mp4", file_name="vi.mp4", public_url="u", status="invalid")

        run_cleanup()

        # Only v_eligible was deleted
        mock_delete.assert_called_once_with("inbox/v_eligible.mp4")
        v_eligible.refresh_from_db()
        self.assertEqual(v_eligible.status, "file_deleted")
        self.assertIsNotNone(v_eligible.file_deleted_at)

        # Others remain untouched
        v_keep.refresh_from_db()
        self.assertEqual(v_keep.status, "posted")
        self.assertIsNone(v_keep.file_deleted_at)

        v_future.refresh_from_db()
        self.assertEqual(v_future.status, "posted")
        self.assertIsNone(v_future.file_deleted_at)

        for v in [v_queued, v_sched, v_await, v_upload, v_failed, v_invalid]:
            v.refresh_from_db()
            self.assertIsNone(v.file_deleted_at)

    def test_activity_log_pruning(self):
        """ActivityLog rows older than 90 days are pruned; newer ones preserved."""
        old_log = ActivityLog.objects.create(event="old_event", message="Old message")
        # Force created_at to 100 days ago
        ActivityLog.objects.filter(id=old_log.id).update(created_at=self.t0 - timedelta(days=100))

        new_log = ActivityLog.objects.create(event="recent_event", message="Recent message")

        run_cleanup()

        self.assertFalse(ActivityLog.objects.filter(id=old_log.id).exists())
        self.assertTrue(ActivityLog.objects.filter(id=new_log.id).exists())
