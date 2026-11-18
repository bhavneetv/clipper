import uuid
from django.db import connection, models


def get_next_video_seq() -> int:
    """Fetch next sequence number from Postgres video_seq_seq or SQLite fallback."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT nextval('video_seq_seq');")
            return cursor.fetchone()[0]
    else:
        # SQLite / test fallback
        last = Video.objects.aggregate(models.Max("seq"))["seq__max"]
        return (last or 0) + 1


class Video(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("scheduled", "Scheduled"),
        ("awaiting_approval", "Awaiting Approval"),
        ("uploading", "Uploading"),
        ("posted", "Posted"),
        ("file_deleted", "File Deleted"),
        ("invalid", "Invalid"),
        ("failed", "Failed"),
    ]

    PLATFORM_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("uploading", "Uploading"),
        ("posted", "Posted"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seq = models.IntegerField(null=True, blank=True)
    file_key = models.CharField(max_length=512, unique=True)
    file_name = models.CharField(max_length=255)
    public_url = models.CharField(max_length=1024)
    file_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    size_bytes = models.BigIntegerField(default=0)
    duration_s = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=24, default="queued", choices=STATUS_CHOICES, db_index=True)
    invalid_reason = models.CharField(max_length=255, null=True, blank=True)

    # Captions
    yt_title = models.CharField(max_length=100, null=True, blank=True)
    yt_description = models.TextField(null=True, blank=True)
    ig_caption = models.TextField(null=True, blank=True)
    hashtags = models.JSONField(null=True, blank=True)
    caption_source_yt = models.CharField(max_length=12, null=True, blank=True)
    caption_source_ig = models.CharField(max_length=12, null=True, blank=True)

    # Flags and ordering
    held = models.BooleanField(default=False)
    keep = models.BooleanField(default=False)
    post_now = models.BooleanField(default=False)
    queue_position = models.IntegerField(default=0)

    # Schedule and approval (UTC)
    custom_post_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    approval_due_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.CharField(max_length=8, null=True, blank=True)  # 'admin' | 'auto'

    # YouTube
    yt_status = models.CharField(max_length=12, default="pending", choices=PLATFORM_STATUS_CHOICES)
    yt_attempts = models.IntegerField(default=0)
    yt_next_attempt_at = models.DateTimeField(null=True, blank=True)
    yt_posted_at = models.DateTimeField(null=True, blank=True)
    yt_video_id = models.CharField(max_length=64, null=True, blank=True)
    yt_url = models.CharField(max_length=255, null=True, blank=True)
    yt_error = models.TextField(null=True, blank=True)

    # Instagram
    ig_status = models.CharField(max_length=12, default="pending", choices=PLATFORM_STATUS_CHOICES)
    ig_attempts = models.IntegerField(default=0)
    ig_next_attempt_at = models.DateTimeField(null=True, blank=True)
    ig_posted_at = models.DateTimeField(null=True, blank=True)
    ig_media_id = models.CharField(max_length=64, null=True, blank=True)
    ig_permalink = models.CharField(max_length=255, null=True, blank=True)
    ig_error = models.TextField(null=True, blank=True)

    # Facebook
    fb_status = models.CharField(max_length=12, default="pending", choices=PLATFORM_STATUS_CHOICES)
    fb_attempts = models.IntegerField(default=0)
    fb_next_attempt_at = models.DateTimeField(null=True, blank=True)
    fb_posted_at = models.DateTimeField(null=True, blank=True)
    fb_video_id = models.CharField(max_length=64, null=True, blank=True)
    fb_url = models.CharField(max_length=255, null=True, blank=True)
    fb_error = models.TextField(null=True, blank=True)
    fb_caption = models.TextField(null=True, blank=True)
    caption_source_fb = models.CharField(max_length=12, null=True, blank=True)

    # Cleanup
    delete_after = models.DateTimeField(null=True, blank=True, db_index=True)
    file_deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "videos"
        ordering = ["queue_position", "created_at"]

    def save(self, *args, **kwargs):
        if self.seq is None:
            self.seq = get_next_video_seq()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.file_name} (#{self.seq})"


class Setting(models.Model):
    k = models.CharField(max_length=64, primary_key=True)
    v = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings"

    def __str__(self):
        return self.k


class Account(models.Model):
    PLATFORM_CHOICES = [
        ("youtube", "YouTube"),
        ("instagram", "Instagram"),
        ("facebook", "Facebook"),
    ]
    STATUS_CHOICES = [
        ("connected", "Connected"),
        ("error", "Error"),
        ("disconnected", "Disconnected"),
    ]

    platform = models.CharField(max_length=16, primary_key=True, choices=PLATFORM_CHOICES)
    account_name = models.CharField(max_length=255, null=True, blank=True)
    access_token_enc = models.TextField(null=True, blank=True)
    refresh_token_enc = models.TextField(null=True, blank=True)
    ig_user_id = models.CharField(max_length=64, null=True, blank=True)
    fb_page_id = models.CharField(max_length=64, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default="disconnected", choices=STATUS_CHOICES)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts"

    def __str__(self):
        return f"{self.platform}: {self.account_name or self.status}"


class ActivityLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    video_id = models.UUIDField(null=True, blank=True, db_index=True)
    event = models.CharField(max_length=48)
    platform = models.CharField(max_length=16, null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "activity_log"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.created_at}] {self.event} - {self.platform or ''}: {self.message or ''}"
