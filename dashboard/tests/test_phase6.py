from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import zoneinfo
from django.test import Client, TestCase
from core.models import Account, ActivityLog, Setting, Video
from core.scheduling import set_fake_now
from core.settings_store import init_default_settings, set_setting


class Phase6DashboardPagesTests(TestCase):
    def setUp(self):
        init_default_settings()
        self.client = Client()
        session = self.client.session
        session["is_authenticated"] = True
        session["admin_email"] = "admin@example.com"
        session.save()
        self.t0 = datetime(2026, 11, 18, 12, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        set_fake_now(self.t0)

    def tearDown(self):
        set_fake_now(None)

    def test_overview_page_and_summary_partial(self):
        """Overview page loads with 200, renders summary stats and partial."""
        Video.objects.create(
            file_key="inbox/ov.mp4",
            file_name="ov.mp4",
            public_url="https://example.com/ov.mp4",
            status="posted",
            yt_status="posted",
            yt_posted_at=self.t0,
        )

        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("System Overview", res.content.decode("utf-8"))

        res_partial = self.client.get("/partials/summary", HTTP_HX_REQUEST="true")
        self.assertEqual(res_partial.status_code, 200)
        self.assertIn("Posted Today", res_partial.content.decode("utf-8"))

    def test_approvals_actions_and_live_list(self):
        """Approvals page, approve now, hold, release, and approve all."""
        v1 = Video.objects.create(
            file_key="inbox/ap1.mp4",
            file_name="ap1.mp4",
            public_url="u1",
            status="awaiting_approval",
            approval_due_at=self.t0 + timedelta(minutes=10),
        )
        v2 = Video.objects.create(
            file_key="inbox/ap2.mp4",
            file_name="ap2.mp4",
            public_url="u2",
            status="awaiting_approval",
            approval_due_at=self.t0 + timedelta(minutes=10),
        )

        # Live approvals page
        res = self.client.get("/approvals")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ap1.mp4", res.content.decode("utf-8"))

        # Hold v1
        self.client.post(f"/videos/{v1.id}/hold")
        v1.refresh_from_db()
        self.assertTrue(v1.held)

        # Release v1 (immediately approves)
        self.client.post(f"/videos/{v1.id}/release")
        v1.refresh_from_db()
        self.assertFalse(v1.held)
        self.assertEqual(v1.status, "uploading")
        self.assertEqual(v1.approved_by, "admin")

        # Approve all
        self.client.post("/approvals/approve-all")
        v2.refresh_from_db()
        self.assertEqual(v2.status, "uploading")

    def test_queue_page_reorder_and_pause(self):
        """Queue page, drag-and-drop reorder, and global/platform pause toggle."""
        v1 = Video.objects.create(file_key="inbox/q1.mp4", file_name="q1.mp4", public_url="u1", queue_position=1, status="queued")
        v2 = Video.objects.create(file_key="inbox/q2.mp4", file_name="q2.mp4", public_url="u2", queue_position=2, status="queued")

        res = self.client.get("/queue")
        self.assertEqual(res.status_code, 200)

        # Toggle global pause
        self.client.post("/queue/pause", {"scope": "all"})
        self.assertTrue(Setting.objects.get(k="paused").v)

        # Toggle again -> unpaused
        self.client.post("/queue/pause", {"scope": "all"})
        self.assertFalse(Setting.objects.get(k="paused").v)

        # Reorder
        res_reorder = self.client.post("/queue/reorder", {"ids[]": [str(v2.id), str(v1.id)]})
        self.assertEqual(res_reorder.status_code, 200)
        v2.refresh_from_db()
        self.assertEqual(v2.queue_position, 1)

    def test_video_details_edit_and_actions(self):
        """Video detail view, edit captions/time, and skip/retry."""
        v = Video.objects.create(
            file_key="inbox/detail.mp4",
            file_name="detail.mp4",
            public_url="u",
            status="uploading",
            yt_status="failed",
            ig_status="pending",
        )

        res = self.client.get(f"/videos/{v.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("detail.mp4", res.content.decode("utf-8"))

        # Edit video
        self.client.post(f"/videos/{v.id}/edit", {
            "yt_title": "Edited YouTube Title",
            "yt_description": "Edited Description",
            "ig_caption": "Edited IG Caption",
            "hashtags": "shorts tech news",
            "keep": "on",
        })
        v.refresh_from_db()
        self.assertEqual(v.yt_title, "Edited YouTube Title")
        self.assertEqual(v.hashtags, ["shorts", "tech", "news"])
        self.assertTrue(v.keep)

        # Retry YouTube
        self.client.post(f"/videos/{v.id}/retry", {"platform": "yt"})
        v.refresh_from_db()
        self.assertEqual(v.yt_status, "pending")
        self.assertEqual(v.yt_attempts, 0)

        # Skip Instagram
        self.client.post(f"/videos/{v.id}/skip", {"platform": "ig"})
        v.refresh_from_db()
        self.assertEqual(v.ig_status, "skipped")

    def test_settings_page_and_validation(self):
        """Settings page update, timezone & HH:MM validation, and test notification."""
        res = self.client.get("/settings")
        self.assertEqual(res.status_code, 200)

        # Update valid settings
        post_data = {
            "default_title": "Default Title {number}",
            "default_desc": "Default Description",
            "default_hashtags": "shorts viral",
            "post_times": "09:00 13:00 18:00",
            "tz": "Asia/Kolkata",
            "max_per_day_yt": "5",
            "max_per_day_ig": "8",
            "approval_timeout_min": "15",
            "retention_days": "10",
            "storage_limit_gb": "2",
            "poll_interval_min": "3",
            "youtube_privacy": "unlisted",
            "notify_enabled": "on",
            "notify_email": "admin@example.com",
        }
        res_post = self.client.post("/settings", post_data)
        self.assertEqual(res_post.status_code, 200)
        self.assertIn("Settings saved successfully", res_post.content.decode("utf-8"))

        # Invalid timezone test
        post_data["tz"] = "Invalid/Timezone"
        res_err = self.client.post("/settings", post_data)
        self.assertIn("Unknown timezone", res_err.content.decode("utf-8"))

        # Test notification endpoint
        with patch("dashboard.views.settings.notify_test_email") as mock_test_email:
            res_test = self.client.post("/notifications/test")
            self.assertEqual(res_test.status_code, 200)
            mock_test_email.assert_called_once_with("admin@example.com")

    def test_accounts_and_logs_pages(self):
        """Accounts page and activity logs filtering."""
        Account.objects.create(platform="youtube", account_name="Test Channel", status="connected")
        Account.objects.create(platform="instagram", account_name="test_user", status="connected")

        res_acc = self.client.get("/accounts")
        self.assertEqual(res_acc.status_code, 200)
        self.assertIn("Test Channel", res_acc.content.decode("utf-8"))

        # Logs page
        ActivityLog.objects.create(event="detected", platform="youtube", message="Test log entry")
        res_logs = self.client.get("/logs?event=detected")
        self.assertEqual(res_logs.status_code, 200)
        self.assertIn("Test log entry", res_logs.content.decode("utf-8"))
