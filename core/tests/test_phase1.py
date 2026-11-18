import time
import bcrypt
from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from core.models import Account, ActivityLog, Setting, Video
from core.security import decrypt_token, encrypt_token
from core.settings_store import get_all_settings, get_setting, init_default_settings, set_setting


class Phase1FoundationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.admin_email = "admin@example.com"
        self.plain_password = "test-password-123"
        # Generate bcrypt hash
        salt = bcrypt.gensalt(rounds=4)  # faster for tests
        self.password_hash = bcrypt.hashpw(self.plain_password.encode("utf-8"), salt).decode("utf-8")

    def test_health_check_unauthenticated(self):
        """GET /health must be public, return 200 and {'ok': True}."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_unauthenticated_redirect_to_login(self):
        """Unauthenticated requests to protected endpoints redirect to /login."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/login")

    def test_unauthenticated_htmx_redirect(self):
        """Unauthenticated HTMX requests return 200 with HX-Redirect header."""
        response = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("HX-Redirect"), "/login")

    def test_login_flow_and_rate_limiting(self):
        """Login flow with bcrypt, rate limiting after 5 failures, and successful auth."""
        with override_settings(ADMIN_EMAIL=self.admin_email, ADMIN_PASSWORD_HASH=self.password_hash):
            # 1. Wrong password failure
            res = self.client.post("/login", {"email": self.admin_email, "password": "wrong"})
            self.assertEqual(res.status_code, 200)
            self.assertIn("Invalid email or password", res.content.decode("utf-8"))

            # 2. 4 more failures to reach limit (total 5)
            for _ in range(4):
                self.client.post("/login", {"email": self.admin_email, "password": "wrong"})

            # 3. 6th attempt should be rate-limited (status 429)
            res_locked = self.client.post("/login", {"email": self.admin_email, "password": self.plain_password})
            self.assertEqual(res_locked.status_code, 429)
            self.assertIn("Too many failed login attempts", res_locked.content.decode("utf-8"))

            # Clear cache to reset rate limit
            cache.clear()

            # 4. Successful login
            res_success = self.client.post("/login", {"email": self.admin_email, "password": self.plain_password})
            self.assertEqual(res_success.status_code, 302)
            self.assertEqual(res_success.url, "/")

            # Session should be authenticated
            session = self.client.session
            self.assertTrue(session.get("is_authenticated"))
            self.assertEqual(session.get("admin_email"), self.admin_email)

    def test_security_token_encryption(self):
        """Fernet encryption and decryption for stored tokens."""
        # Key must be 32 base64 url-safe bytes
        test_key = "aFpjQ09aU0VjU2t2WHZJUkFjVjNZVFlqU1d0VHJ0WFE="
        with override_settings(ENCRYPTION_KEY=test_key):
            secret = "ya29.a0AfH6SMTestToken12345"
            enc = encrypt_token(secret)
            self.assertNotEqual(secret, enc)
            dec = decrypt_token(enc)
            self.assertEqual(secret, dec)

            # None and empty return same
            self.assertIsNone(encrypt_token(None))
            self.assertIsNone(decrypt_token(None))
            self.assertEqual(encrypt_token(""), "")
            self.assertEqual(decrypt_token(""), "")

    def test_models_and_updated_at(self):
        """Model creation, seq generation, and updated_at changes on update."""
        v = Video.objects.create(
            file_key="inbox/video1.mp4",
            file_name="video1.mp4",
            public_url="https://supabase.co/storage/v1/object/public/shorts/inbox/video1.mp4",
        )
        self.assertIsNotNone(v.id)
        self.assertIsNotNone(v.seq)
        first_updated_at = v.updated_at

        time.sleep(0.01)
        v.yt_title = "My New Shorts Title"
        v.save()
        v.refresh_from_db()
        self.assertGreaterEqual(v.updated_at, first_updated_at)

        # Test Setting model
        init_default_settings()
        self.assertEqual(get_setting("tz"), "Asia/Kolkata")
        set_setting("tz", "UTC")
        self.assertEqual(get_setting("tz"), "UTC")

        # Test Account model
        acc = Account.objects.create(
            platform="youtube",
            account_name="Test Channel",
            status="connected",
        )
        self.assertEqual(str(acc), "youtube: Test Channel")

        # Test ActivityLog model
        log = ActivityLog.objects.create(
            video_id=v.id,
            event="detected",
            message="Test detected event",
        )
        self.assertIn("detected", str(log))
