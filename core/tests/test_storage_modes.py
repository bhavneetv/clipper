"""
Tests for Multi-Backend Storage (Local, Cloudinary, Supabase) and IST Timezone logic.
"""
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import zoneinfo
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from core.jobs import run_watcher
from core.models import ActivityLog, Video
from core.scheduling import find_next_free_slot, get_tz
from core.storage import (
    create_signed_upload_url,
    delete_file,
    get_local_file_path,
    get_local_inbox_dir,
    get_public_url,
    get_storage_backend,
    list_inbox_videos,
)
from core.youtube import upload_video_to_youtube
from unittest.mock import MagicMock, patch


class TimezoneAndStorageModeTests(TestCase):
    def test_ist_timezone_configured(self):
        """Verify IST (Asia/Kolkata) is default timezone and provides +05:30 offset."""
        tz = get_tz()
        self.assertEqual(tz.key, "Asia/Kolkata")

        now_utc = datetime(2026, 11, 20, 10, 0, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        now_ist = now_utc.astimezone(tz)
        # 10:00 UTC == 15:30 IST (+5:30)
        self.assertEqual(now_ist.hour, 15)
        self.assertEqual(now_ist.minute, 30)

    def test_local_storage_backend_listing_and_deletion(self):
        """Verify local folder inbox scanner and deletion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            with override_settings(STORAGE_BACKEND="local", LOCAL_INBOX_DIR=tmppath):
                # Create 2 mp4 files and 1 txt file
                (tmppath / "short1.mp4").write_bytes(b"dummy1")
                (tmppath / "short2.mp4").write_bytes(b"dummy2_longer")
                (tmppath / "notes.txt").write_bytes(b"ignore this")

                items = list_inbox_videos()
                names = [i["name"] for i in items]
                self.assertIn("short1.mp4", names)
                self.assertIn("short2.mp4", names)
                self.assertNotIn("notes.txt", names)

                # Test public URL
                pub_url = get_public_url("local:short1.mp4")
                self.assertIn("/media/local/short1.mp4", pub_url)

                # Test delete
                success = delete_file("local:short1.mp4")
                self.assertTrue(success)
                self.assertFalse((tmppath / "short1.mp4").exists())

    def test_local_video_media_and_upload_views(self):
        """Test streaming local video endpoint and uploading to local inbox."""
        client = Client()
        session = client.session
        session["is_authenticated"] = True
        session.save()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            test_file = tmppath / "demo.mp4"
            test_file.write_bytes(b"test video binary content")

            with override_settings(STORAGE_BACKEND="local", LOCAL_INBOX_DIR=tmppath):
                # 1. Test media streaming
                res = client.get("/media/local/demo.mp4")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res["Content-Type"], "video/mp4")
                res.close()

                # 2. Test uploading file
                with open(test_file, "rb") as fh:
                    upload_res = client.post(
                        "/uploads/local",
                        {"file": fh, "file_name": "uploaded_demo.mp4"},
                    )
                self.assertEqual(upload_res.status_code, 200)
                self.assertTrue((tmppath / "uploaded_demo.mp4").exists())

    @patch("core.youtube.get_valid_youtube_access_token")
    @patch("requests.post")
    @patch("requests.put")
    def test_youtube_native_scheduled_upload(self, mock_put, mock_post, mock_token):
        """Verify upload_video_to_youtube passes publishAt in status and streams from local disk."""
        mock_token.return_value = "fake_access_token"

        # Mock init session
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.headers = {"Location": "https://upload.youtube.com/resumable/test"}
        mock_post.return_value = init_response

        # Mock chunk upload
        chunk_response = MagicMock()
        chunk_response.status_code = 200
        chunk_response.json.return_value = {"id": "test_video_123"}
        mock_put.return_value = chunk_response

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            test_file = tmppath / "sample.mp4"
            test_file.write_bytes(b"sample mp4 video binary content")

            with override_settings(STORAGE_BACKEND="local", LOCAL_INBOX_DIR=tmppath):
                video = Video.objects.create(
                    file_key="local:sample.mp4",
                    file_name="sample.mp4",
                    public_url="http://localhost:8080/media/local/sample.mp4",
                    size_bytes=len(test_file.read_bytes()),
                    status="queued",
                )

                schedule_time = datetime(2026, 11, 20, 18, 0, 0, tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
                success, err, data = upload_video_to_youtube(video, publish_at=schedule_time)

                self.assertTrue(success, f"Upload failed with: {err}")
                self.assertEqual(data["video_id"], "test_video_123")

                # Verify POST payload contained status with privacyStatus='private' and publishAt
                call_kwargs = mock_post.call_args[1]
                sent_status = call_kwargs["json"]["status"]
                self.assertEqual(sent_status["privacyStatus"], "private")
                self.assertIn("publishAt", sent_status)
                # 18:00 IST is 12:30 UTC
                self.assertEqual(sent_status["publishAt"], "2026-11-20T12:30:00.000Z")

    def test_cron_trigger_endpoint_authentication_and_execution(self):
        """Verify /api/cron/tick endpoint enforces secret key and runs background tasks."""
        client = Client()

        # 1. Test unauthenticated without key -> 401
        res = client.get("/api/cron/tick")
        self.assertEqual(res.status_code, 401)

        # 2. Test with invalid key -> 401
        res = client.get("/api/cron/tick?key=wrong_key")
        self.assertEqual(res.status_code, 401)

        # 3. Test with valid key -> 200 and actions returned
        from core.settings_store import get_setting
        cron_secret = get_setting("cron_secret", "shorts_cron_key_123456")

        with patch("dashboard.views.cron.run_watcher") as mock_watcher, \
             patch("dashboard.views.cron.run_tick") as mock_tick, \
             patch("dashboard.views.cron.run_uploader") as mock_uploader:
            res = client.get(f"/api/cron/tick?key={cron_secret}")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["ok"])
            self.assertIn("watcher_scanned", data["actions"])
            self.assertIn("tick_processed", data["actions"])
            self.assertIn("uploader_dispatched", data["actions"])
            mock_watcher.assert_called_once()
            mock_tick.assert_called_once()
            mock_uploader.assert_called_once()

    def test_storage_usage_stats_helper(self):
        """Verify get_storage_usage_stats returns expected structure."""
        from core.storage import get_storage_usage_stats
        stats = get_storage_usage_stats()
        self.assertIn("active_backend", stats)
        self.assertIn("local", stats)
        self.assertIn("supabase", stats)
        self.assertIn("cloudinary", stats)
        self.assertIn("database", stats)
        self.assertIsInstance(stats["local"]["used_mb"], float)
