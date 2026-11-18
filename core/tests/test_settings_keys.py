from django.test import Client, TestCase
from core.models import Setting
from core.settings_store import get_config_value, set_setting, sync_env_to_db


class SettingsKeysDbTests(TestCase):
    def test_get_config_value_db_override(self):
        """DB setting value takes precedence over environment defaults."""
        set_setting("cloudinary_cloud_name", "my_custom_db_cloud")
        val = get_config_value("cloudinary_cloud_name")
        self.assertEqual(val, "my_custom_db_cloud")

    def test_sync_env_to_db(self):
        """sync_env_to_db imports credentials into Setting table."""
        sync_env_to_db()
        obj = Setting.objects.filter(k="smtp_host").first()
        self.assertIsNotNone(obj)
        self.assertIn("gmail", obj.v)

    def test_save_settings_via_view(self):
        """Submitting settings form saves credentials directly into the database."""
        c = Client()
        session = c.session
        session["is_authenticated"] = True
        session.save()

        data = {
            "default_title": "Test Title",
            "default_desc": "Test Desc",
            "post_times": "10:00 14:00",
            "tz": "Asia/Kolkata",
            "max_per_day_yt": "5",
            "max_per_day_ig": "8",
            "max_per_day_fb": "7",
            "approval_timeout_min": "10",
            "retention_days": "7",
            "storage_limit_gb": "1",
            "poll_interval_min": "5",
            "youtube_privacy": "public",
            "storage_backend": "cloudinary",
            "cloudinary_cloud_name": "test_cloud_123",
            "cloudinary_api_key": "999888777",
            "cloudinary_api_secret": "secret_abc_123",
            "supabase_url": "https://test.supabase.co",
            "supabase_service_role_key": "service_role_key_test",
            "supabase_bucket": "my_bucket",
            "smtp_host": "smtp.test.com",
            "smtp_port": "587",
            "smtp_user": "tester@test.com",
            "smtp_password": "mypassword123",
            "smtp_from": "tester@test.com",
            "smtp_starttls": "on",
            "google_client_id": "google_id_test.apps.googleusercontent.com",
            "google_client_secret": "google_secret_test",
            "google_redirect_uri": "http://localhost:8080/callback",
        }

        res = c.post("/settings", data, HTTP_HOST="localhost")
        self.assertEqual(res.status_code, 200)

        # Verify database Setting entries
        self.assertEqual(get_config_value("cloudinary_cloud_name"), "test_cloud_123")
        self.assertEqual(get_config_value("cloudinary_api_key"), "999888777")
        self.assertEqual(get_config_value("cloudinary_api_secret"), "secret_abc_123")
        self.assertEqual(get_config_value("supabase_url"), "https://test.supabase.co")
        self.assertEqual(get_config_value("supabase_service_role_key"), "service_role_key_test")
        self.assertEqual(get_config_value("supabase_bucket"), "my_bucket")
        self.assertEqual(get_config_value("smtp_host"), "smtp.test.com")
        self.assertEqual(get_config_value("smtp_port"), 587)
        self.assertEqual(get_config_value("smtp_user"), "tester@test.com")
        self.assertEqual(get_config_value("smtp_password"), "mypassword123")
        self.assertEqual(get_config_value("google_client_id"), "google_id_test.apps.googleusercontent.com")
