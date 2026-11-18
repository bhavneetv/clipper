from datetime import datetime, timedelta
import zoneinfo
from django.test import TestCase
from core.captions import resolve_instagram_caption, resolve_youtube_caption
from core.models import ActivityLog, Video
from core.scheduling import (
    find_next_free_slot,
    get_now,
    hold_video,
    post_now_video,
    release_video,
    reorder_queue,
    run_tick,
    set_fake_now,
)
from core.settings_store import init_default_settings, set_setting


class Phase3CaptionsAndSchedulingTests(TestCase):
    def setUp(self):
        init_default_settings()
        set_setting("tz", "UTC")
        set_setting("post_times", ["10:00", "14:00", "19:00"])
        set_setting("approval_timeout_min", 10)
        set_setting("default_caption", {
            "title": "Default Title for {filename}",
            "description": "Default Description #{number}",
            "hashtags": ["shorts", "viral"],
        })
        set_fake_now(None)

    def tearDown(self):
        set_fake_now(None)

    def test_caption_resolution_hierarchy(self):
        """A video with no caption uses default, replaces placeholders, and formats correctly."""
        v = Video.objects.create(
            file_key="inbox/ep01.mp4",
            file_name="ep01.mp4",
            public_url="https://example.com/ep01.mp4",
        )
        yt_cap, yt_src = resolve_youtube_caption(v)
        self.assertEqual(yt_src, "default")
        self.assertEqual(yt_cap["title"], "Default Title for ep01")
        self.assertIn(f"Default Description #{v.seq}", yt_cap["description"])
        self.assertIn("#Shorts", yt_cap["description"])
        self.assertIn("viral", yt_cap["tags"])

        ig_cap, ig_src = resolve_instagram_caption(v)
        self.assertEqual(ig_src, "default")
        self.assertIn(f"Default Description #{v.seq}", ig_cap)
        self.assertIn("#shorts #viral", ig_cap)

        # Video with own caption overrides default
        v.yt_title = "Own Title {number}"
        v.yt_description = "Own Desc"
        v.save()
        yt_cap2, yt_src2 = resolve_youtube_caption(v)
        self.assertEqual(yt_src2, "video")
        self.assertEqual(yt_cap2["title"], f"Own Title {v.seq}")
        self.assertIn("Own Desc", yt_cap2["description"])

    def test_consecutive_slots_across_days(self):
        """10 videos get consecutive slots across multiple days (3 slots/day)."""
        # Start clock at 2026-11-18 08:00 UTC
        start_time = datetime(2026, 11, 18, 8, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        set_fake_now(start_time)

        videos = []
        for i in range(10):
            videos.append(Video.objects.create(
                file_key=f"inbox/vid_{i}.mp4",
                file_name=f"vid_{i}.mp4",
                public_url=f"https://example.com/vid_{i}.mp4",
                queue_position=i + 1,
                status="queued",
            ))

        # Run tick to assign slots
        run_tick()

        scheduled = list(Video.objects.filter(id__in=[v.id for v in videos]).order_by("queue_position"))
        for v in scheduled:
            self.assertEqual(v.status, "scheduled")
            self.assertIsNotNone(v.scheduled_at)

        # Slots expected:
        # Day 1 (Nov 18): 10:00, 14:00, 19:00
        # Day 2 (Nov 19): 10:00, 14:00, 19:00
        # Day 3 (Nov 20): 10:00, 14:00, 19:00
        # Day 4 (Nov 21): 10:00
        expected_slots = [
            datetime(2026, 11, 18, 10, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 18, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 18, 19, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 19, 10, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 19, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 19, 19, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 20, 10, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 20, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 20, 19, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
            datetime(2026, 11, 21, 10, 0, tzinfo=zoneinfo.ZoneInfo("UTC")),
        ]

        actual_slots = [v.scheduled_at for v in scheduled]
        self.assertEqual(actual_slots, expected_slots)

    def test_approval_gate_countdown_and_auto_approve(self):
        """
        Countdown starts at scheduled_at.
        An untouched video auto-approves exactly after timeout.
        A held video does not auto-approve until released.
        Post-now skips the wait.
        """
        t0 = datetime(2026, 11, 18, 10, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        set_fake_now(t0)

        v1 = Video.objects.create(
            file_key="inbox/v1.mp4",
            file_name="v1.mp4",
            public_url="https://example.com/v1.mp4",
            scheduled_at=t0,
            status="scheduled",
        )

        v2 = Video.objects.create(
            file_key="inbox/v2.mp4",
            file_name="v2.mp4",
            public_url="https://example.com/v2.mp4",
            scheduled_at=t0,
            status="scheduled",
            held=True,
        )

        v3 = Video.objects.create(
            file_key="inbox/v3.mp4",
            file_name="v3.mp4",
            public_url="https://example.com/v3.mp4",
            status="queued",
        )

        # 1. Tick at scheduled time: gate opens, countdown starts at scheduled_at
        run_tick()
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v1.status, "awaiting_approval")
        self.assertEqual(v1.approval_due_at, t0 + timedelta(minutes=10))
        self.assertEqual(v2.status, "awaiting_approval")

        # 2. Advance clock 5 minutes: still awaiting approval
        set_fake_now(t0 + timedelta(minutes=5))
        run_tick()
        v1.refresh_from_db()
        self.assertEqual(v1.status, "awaiting_approval")

        # 3. Advance clock to exact timeout (10 minutes): v1 auto-approves, v2 stays held
        set_fake_now(t0 + timedelta(minutes=10))
        run_tick()
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v1.status, "uploading")
        self.assertEqual(v1.approved_by, "auto")
        self.assertEqual(v2.status, "awaiting_approval")

        # 4. Release held video v2: immediately approved
        release_video(v2)
        v2.refresh_from_db()
        self.assertEqual(v2.status, "uploading")
        self.assertEqual(v2.approved_by, "admin")
        self.assertFalse(v2.held)

        # 5. Post-now video v3: skips wait and approves immediately
        post_now_video(v3)
        v3.refresh_from_db()
        self.assertEqual(v3.status, "uploading")
        self.assertEqual(v3.approved_by, "admin")
        self.assertTrue(v3.post_now)

    def test_queue_reordering(self):
        """Reordering queue resets slots and recalculates them on next tick."""
        t0 = datetime(2026, 11, 18, 8, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        set_fake_now(t0)

        v1 = Video.objects.create(file_key="inbox/q1.mp4", file_name="q1.mp4", public_url="u1", queue_position=1, status="queued")
        v2 = Video.objects.create(file_key="inbox/q2.mp4", file_name="q2.mp4", public_url="u2", queue_position=2, status="queued")
        run_tick()

        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v1.scheduled_at, datetime(2026, 11, 18, 10, 0, tzinfo=zoneinfo.ZoneInfo("UTC")))
        self.assertEqual(v2.scheduled_at, datetime(2026, 11, 18, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC")))

        # Invert order
        reorder_queue([str(v2.id), str(v1.id)])
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v2.queue_position, 1)
        self.assertEqual(v1.queue_position, 2)
        self.assertEqual(v1.status, "queued")
        self.assertIsNone(v1.scheduled_at)

        # Re-tick assigns earlier slot to v2
        run_tick()
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v2.scheduled_at, datetime(2026, 11, 18, 10, 0, tzinfo=zoneinfo.ZoneInfo("UTC")))
        self.assertEqual(v1.scheduled_at, datetime(2026, 11, 18, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC")))
