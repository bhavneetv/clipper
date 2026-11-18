from django.urls import path
from dashboard.views import accounts, approvals, auth, cron, logs, overview, queue, settings, videos

urlpatterns = [
    # Auth
    path("login", auth.login_view, name="login"),
    path("logout", auth.logout_view, name="logout"),

    # Overview
    path("", overview.overview_view, name="overview"),
    path("partials/summary", overview.summary_partial_view, name="summary_partial"),

    # Approvals
    path("approvals", approvals.approvals_view, name="approvals"),
    path("partials/approvals", approvals.approvals_view, name="partials_approvals"),
    path("approvals/approve-all", approvals.approve_all_view, name="approvals_approve_all"),

    # Videos
    path("videos", videos.videos_list_view, name="videos_list"),
    path("videos/<uuid:video_id>/", approvals.video_detail_view, name="video_detail"),
    path("videos/<uuid:video_id>/edit", approvals.video_edit_view, name="video_edit"),
    path("videos/<uuid:video_id>/approve", approvals.video_approve_view, name="video_approve"),
    path("videos/<uuid:video_id>/hold", approvals.video_hold_view, name="video_hold"),
    path("videos/<uuid:video_id>/release", approvals.video_release_view, name="video_release"),
    path("videos/<uuid:video_id>/post-now", approvals.video_post_now_view, name="video_post_now"),
    path("videos/<uuid:video_id>/retry", approvals.video_retry_view, name="video_retry"),
    path("videos/<uuid:video_id>/skip", approvals.video_skip_view, name="video_skip"),
    path("videos/<uuid:video_id>/toggle-platform", approvals.video_toggle_platform_view, name="video_toggle_platform"),
    path("videos/<uuid:video_id>/delete", approvals.video_delete_view, name="video_delete"),
    path("videos/bulk", approvals.videos_bulk_action_view, name="videos_bulk"),
    path("videos/scan", videos.scan_inbox_view, name="videos_scan"),
    path("uploads/presign", videos.presign_upload_view, name="uploads_presign"),
    path("uploads/local", videos.local_video_upload_view, name="uploads_local"),
    path("media/local/<str:filename>", videos.local_video_media_view, name="local_video_media"),

    # Queue
    path("queue", queue.queue_view, name="queue"),
    path("queue/reorder", queue.queue_reorder_view, name="queue_reorder"),
    path("queue/pause", queue.queue_pause_view, name="queue_pause"),
    path("queue/bulk-platform", queue.queue_bulk_platform_view, name="queue_bulk_platform"),

    # Settings
    path("settings", settings.settings_view, name="settings"),
    path("settings/storage/purge", settings.storage_purge_view, name="storage_purge"),
    path("settings/shutdown/toggle", settings.shutdown_toggle_view, name="shutdown_toggle"),
    path("notifications/test", settings.test_notification_view, name="notifications_test"),

    # Accounts
    path("accounts", accounts.accounts_view, name="accounts"),
    path("accounts/youtube/connect", accounts.youtube_connect_view, name="youtube_connect"),
    path("accounts/youtube/callback", accounts.youtube_callback_view, name="youtube_callback"),
    path("accounts/instagram/token", accounts.instagram_token_view, name="instagram_token"),
    path("accounts/facebook/token", accounts.facebook_token_view, name="facebook_token"),
    path("accounts/<str:platform>/test", accounts.account_test_view, name="account_test"),

    # Logs
    path("logs", logs.logs_view, name="logs"),

    # External Cron Trigger
    path("api/cron/tick", cron.cron_trigger_view, name="cron_trigger"),
]
