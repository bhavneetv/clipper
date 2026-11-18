from unittest.mock import MagicMock, patch
from django.test import Client, TestCase
from core.facebook import get_valid_facebook_credentials, publish_video_to_facebook, save_facebook_credentials
from core.models import Account, Video
from core.settings_store import init_default_settings


class FacebookIntegrationTests(TestCase):
    def setUp(self):
        init_default_settings()

    @patch("requests.get")
    def test_save_facebook_credentials_success(self, mock_get):
        """Test verifying and saving Facebook Page credentials."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "name": "My Viral Videos",
            "access_token": "EAA_PAGE_TOKEN_123",
            "id": "100200300400",
        }
        mock_get.return_value = mock_resp

        success, err = save_facebook_credentials("EAA_USER_OR_PAGE_TOKEN", "100200300400")
        self.assertTrue(success)
        self.assertIsNone(err)

        acc = Account.objects.filter(platform="facebook").first()
        self.assertIsNotNone(acc)
        self.assertEqual(acc.account_name, "My Viral Videos")
        self.assertEqual(acc.fb_page_id, "100200300400")
        self.assertEqual(acc.status, "connected")

        token, page_id = get_valid_facebook_credentials()
        self.assertEqual(token, "EAA_PAGE_TOKEN_123")
        self.assertEqual(page_id, "100200300400")

    @patch("requests.get")
    def test_save_facebook_credentials_failure(self, mock_get):
        """Test invalid Facebook Page credentials returns error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": {"message": "Invalid OAuth access token."}}
        mock_get.return_value = mock_resp

        success, err = save_facebook_credentials("INVALID_TOKEN", "100200300400")
        self.assertFalse(success)
        self.assertIn("Invalid OAuth access token", err)

    @patch("requests.post")
    @patch("core.facebook.get_valid_facebook_credentials")
    def test_publish_video_to_facebook_cloud_url(self, mock_creds, mock_post):
        """Test publishing video with cloud URL to Facebook."""
        mock_creds.return_value = ("EAA_TOKEN", "100200300400")

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"id": "999888777"}
        mock_post.return_value = mock_post_resp

        v = Video.objects.create(
            file_key="inbox/video1.mp4",
            file_name="video1.mp4",
            public_url="https://res.cloudinary.com/demo/video/upload/video1.mp4",
            status="uploading",
        )

        success, err, data = publish_video_to_facebook(v)
        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertEqual(data["video_id"], "999888777")
        self.assertIn("999888777", data["permalink"])
