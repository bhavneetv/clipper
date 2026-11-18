# Shorts Auto-Poster: Build Specification

**For:** an AI coding agent (Antigravity). Build this project end to end from this file.
**Owner:** Bhavneet Verma. **Single admin user.** Version 1.3.

**Shape of the project:** one Django application (backend logic, scheduler, and the admin dashboard as Django templates) deployed as **one Render web service**. Supabase provides both the database (Postgres) and file storage (Storage bucket). There is no separate frontend project, no React, no Netlify, no CORS.

How to work: build in the phases of section 14, in order. After each phase, run the checks listed for it, fix failures, then continue. Do not ask questions; where something is unspecified, use the defaults in this file and note the choice in `README.md`. Keep the code simple, typed, and commented where logic is non-obvious.

---

## 1. What the product does

The owner drops 10 to 20 finished short videos (MP4) into a Supabase Storage bucket in one go. The system:

1. Detects the new files and validates them.
2. Gives each video a posting time from the default posting times in settings (unless the admin overrides it).
3. When a video's scheduled time starts, shows it in the admin panel as **awaiting approval** with a countdown (default 10 minutes). The admin can approve, edit the caption, hold, or skip a platform. **If the admin does nothing, it posts automatically when the countdown ends.**
4. Posts it to the owner's **YouTube (Shorts)** and **Instagram (Reels)** accounts as two independent jobs, with retries.
5. Sends an **email (SMTP)** for every successful and every failed post (if notifications are on).
6. Deletes the file from storage **7 days after a successful post**.
7. Shows everything in the built-in **Django admin dashboard** (same service, same URL).

Captions are **always written by the admin**. There is **no AI** caption generation anywhere. The video-to-shorts generator already exists and is out of scope.

## 2. Tech stack (fixed)

| Part | Choice |
|------|--------|
| App | Python 3.12, **Django 5** (ORM, templates, sessions, CSRF), served by **gunicorn** with **one worker** and a few threads |
| Scheduler | **APScheduler** `BackgroundScheduler`, started inside the Django process (single instance, single gunicorn worker) |
| Database | **Supabase Postgres**, via one connection string (`dj-database-url`, `psycopg` 3), Session pooler, TLS on |
| Storage | **Supabase Storage** (public bucket), accessed with `supabase-py` and the service role key. Files are streamed from the public URL |
| Dashboard UI | Django templates + **Tailwind CSS** (standalone CLI build step in the Dockerfile) + **HTMX** (partial updates and polling) + **Alpine.js** (small interactions, countdown) + **SortableJS** (drag-and-drop queue). All JS from a CDN or vendored into `static/` |
| Static files | **WhiteNoise** (served by the same service) |
| Email | SMTP (Django's email backend or `smtplib`) |
| Video checks | `ffprobe` (ffmpeg) |
| Hosting | **One Render free-tier web service (Docker)** for everything. A **cron job** calls `GET /health` every 10 minutes so Render does not sleep |

Constraints that shape the code:
- Render free tier has about 512 MB RAM. **Never load a whole video into memory.** Stream downloads from the public URL and use chunked/resumable uploads. Keep gunicorn at `--workers 1 --threads 4`.
- Do not rely on the local disk surviving restarts. All state is in Supabase Postgres and Supabase Storage. Temp files go in `/tmp` and are deleted after use.
- Run **one** instance and **one** gunicorn worker only (so the scheduler runs once). Use claim-by-update (section 7.4) so a job is never run twice even during a brief deploy overlap.
- All timestamps are stored in **UTC** (`USE_TZ = True`, `TIMESTAMPTZ`). Convert with the `tz` setting (default `Asia/Kolkata`) for scheduling and display.
- **Database connection:** Render's free tier is IPv4 only, and Supabase's direct connection host is IPv6 only. Use the Supabase **Session pooler** connection string (`...pooler.supabase.com:5432`, user `postgres.<project-ref>`), not the direct host and not the transaction pooler (port 6543). Set `CONN_MAX_AGE=60` and `CONN_HEALTH_CHECKS=True`. Scheduler jobs run in background threads, so call `django.db.close_old_connections()` at the start and end of every job.
- Supabase free plan: 500 MB database, 1 GB storage. The database holds only metadata and logs (small); `activity_log` is pruned (see 7.5).

## 3. Repository layout

```
shorts-autoposter/
├─ README.md
├─ Dockerfile                    # python:3.12-slim + ffmpeg + tailwind build + collectstatic
├─ entrypoint.sh                 # migrate, then gunicorn
├─ render.yaml                   # optional: one web service
├─ requirements.txt
├─ manage.py
├─ .env.example
├─ scripts/gen_secrets.py        # ADMIN_PASSWORD_HASH, DJANGO_SECRET_KEY, ENCRYPTION_KEY
├─ config/
│  ├─ settings.py                # env-driven settings, WhiteNoise, sessions, security flags
│  ├─ urls.py
│  └─ wsgi.py
├─ core/                         # domain logic (no HTML here)
│  ├─ apps.py                    # CoreConfig.ready() starts the scheduler (guarded, see section 8)
│  ├─ models.py                  # Video, Setting, Account, ActivityLog
│  ├─ migrations/                # includes RunSQL: sequence, updated_at triggers, RLS
│  ├─ security.py                # Fernet encrypt/decrypt for tokens
│  ├─ settings_store.py          # get/set settings with defaults
│  ├─ captions.py                # caption resolution + placeholders
│  ├─ scheduling.py              # slot assignment, approval gate logic
│  ├─ storage.py                 # Supabase Storage: list/delete/signed upload URL, public URL
│  ├─ video_probe.py             # ffprobe validation
│  ├─ youtube.py                 # OAuth + resumable upload
│  ├─ instagram.py               # Graph API Reels publish + token refresh
│  ├─ notifier.py                # SMTP emails
│  ├─ jobs.py                    # watcher, tick, uploader, cleanup, token refresh, storage check
│  └─ tests/
├─ dashboard/                    # the UI layer
│  ├─ views/                     # auth, overview, approvals, videos, queue, settings, accounts, logs, uploads
│  ├─ middleware.py              # login required for everything except /health and /login
│  ├─ urls.py
│  ├─ forms.py                   # validated forms (settings, caption edit, login)
│  ├─ templates/dashboard/       # base.html, login, overview, approvals, videos, video_detail, queue, settings, logs
│  │  └─ partials/               # HTMX fragments (approval cards, video rows, stat cards)
│  ├─ static/dashboard/          # tailwind input.css, app.js (countdown, sortable), vendored libs
│  └─ tests/
└─ docs/PRD.md                   # optional copy of the product requirements
```

## 4. Environment variables (`.env.example`)

```
# Django
DJANGO_SECRET_KEY=                               # long random string
DJANGO_DEBUG=false
APP_PUBLIC_URL=https://your-app.onrender.com     # used for OAuth redirect, email links, CSRF
ALLOWED_HOSTS=your-app.onrender.com
CSRF_TRUSTED_ORIGINS=https://your-app.onrender.com
RUN_SCHEDULER=1                                  # set 0 for tests and one-off commands
SESSION_COOKIE_AGE_HOURS=24

# Supabase Postgres (Django / psycopg 3).
# Use the SESSION POOLER string from Supabase > Project Settings > Database > Connection string.
# Host looks like aws-0-<region>.pooler.supabase.com, port 5432, user postgres.<project-ref>.
DATABASE_URL=postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require

# Supabase Storage (same project; public bucket, free plan: 1 GB total, 50 MB per file)
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=                       # server only, never rendered into any page
SUPABASE_BUCKET=shorts                           # must be a PUBLIC bucket
STORAGE_INBOX_PREFIX=inbox/
MAX_FILE_MB=50

# Admin login (single admin)
ADMIN_EMAIL=
ADMIN_PASSWORD_HASH=                             # bcrypt hash

# Encryption key for stored OAuth tokens (Fernet, 32 url-safe base64 bytes)
ENCRYPTION_KEY=

# Google / YouTube
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=${APP_PUBLIC_URL}/accounts/youtube/callback

# Meta / Instagram
META_APP_ID=
META_APP_SECRET=
GRAPH_API_VERSION=v21.0                          # set to a currently supported version

# SMTP
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_STARTTLS=true
```

Rules: secrets only via environment variables, never in code, logs, templates, or responses. The browser never receives any Supabase key.

## 5. Database (Supabase Postgres, Django models)

Models live in `core/models.py` and are created by normal Django migrations (`python manage.py migrate`, run by `entrypoint.sh` on every start; it is idempotent). Use UUID primary keys (`UUIDField(default=uuid.uuid4)`), `JSONField` for JSON, `BooleanField`, and timezone-aware `DateTimeField`s. `updated_at` uses `auto_now=True`, and the migration also installs a database trigger so `QuerySet.update()` calls keep it current.

The fields below define the `Video` model (same names in the database):

```
Video
  id               UUID pk
  seq              int, from DB sequence video_seq_seq (for the {number} placeholder, never reused)
  file_key         varchar(512), unique
  file_name        varchar(255)
  public_url       varchar(1024)
  file_hash        varchar(64) null            # sha256 or storage ETag, duplicate detection; indexed
  size_bytes       bigint default 0
  duration_s       decimal(8,2) null
  width, height    int null
  status           varchar(24) default 'queued'   # indexed
  invalid_reason   varchar(255) null

  # captions written by the admin (all nullable; resolved at post time, see 7.1)
  yt_title         varchar(100) null
  yt_description   text null
  ig_caption       text null
  hashtags         json null                   # list of strings, per-video override
  caption_source_yt, caption_source_ig  varchar(12) null   # video | platform | default

  # flags and ordering
  held, keep, post_now   bool default false
  queue_position   int default 0

  # schedule and approval (UTC)
  custom_post_at, scheduled_at (indexed), approval_due_at, approved_at   datetime null
  approved_by      varchar(8) null             # admin | auto

  # YouTube
  yt_status        varchar(12) default 'pending'   # pending|uploading|posted|failed|skipped
  yt_attempts      int default 0
  yt_next_attempt_at, yt_posted_at   datetime null
  yt_video_id      varchar(64) null
  yt_url           varchar(255) null
  yt_error         text null

  # Instagram
  ig_status        varchar(12) default 'pending'
  ig_attempts      int default 0
  ig_next_attempt_at, ig_posted_at   datetime null
  ig_media_id      varchar(64) null
  ig_permalink     varchar(255) null
  ig_error         text null

  # cleanup
  delete_after (indexed), file_deleted_at   datetime null

  created_at (auto_now_add), updated_at (auto_now)

Setting          k varchar(64) pk, v json, updated_at
Account          platform varchar(16) pk (youtube | instagram), account_name, access_token_enc (text),
                 refresh_token_enc (text), ig_user_id, expires_at, status (connected|error|disconnected), updated_at
ActivityLog      id bigint identity pk, video_id uuid null (indexed), event varchar(48),
                 platform varchar(16) null, message text null, created_at (indexed)
                 events: detected, invalid, scheduled, awaiting_approval, approved, held, released,
                 uploading, posted, failed, retry, deleted, email_sent, email_failed, token_refreshed, ...
```

**Custom migration (`RunSQL`, idempotent)** must also:

```sql
CREATE SEQUENCE IF NOT EXISTS video_seq_seq;

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
-- then, for each of the tables videos, settings, accounts (use the real Django table names):
--   DROP TRIGGER IF EXISTS trg_<t>_updated ON <t>;
--   CREATE TRIGGER trg_<t>_updated BEFORE UPDATE ON <t> FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Lock the tables away from Supabase's public REST API (anon / authenticated keys).
-- RLS on with NO policies = no access via PostgREST. The app connects as the postgres role
-- through the pooler, which bypasses RLS, so it is unaffected.
-- ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;   for videos, settings, accounts, activity_log
-- also enable it on Django's own tables (auth, sessions, migrations, content types).
```

Notes for the code:
- Assign `seq` with `SELECT nextval('video_seq_seq')` when inserting a video, so concurrent inserts never collide.
- Settings are written with an upsert (`update_or_create` or `INSERT ... ON CONFLICT (k) DO UPDATE`).
- Insert detected videos with `bulk_create(..., ignore_conflicts=True)` or `get_or_create` on `file_key`, so overlapping scans never create duplicates.
- Never put the encrypted token fields in any template, context, or response.
- Django sessions use the database backend (`django.contrib.sessions`), so logins survive a Render restart.

### Settings keys and defaults (table `settings`, JSON values)

| Key | Default | Meaning |
|-----|---------|---------|
| `default_caption` | `{"title":"","description":"","hashtags":[]}` | The default caption, used when nothing more specific exists. **Must be set before the first post** (the dashboard warns if it is empty) |
| `caption_yt` | `null` | Optional YouTube-only shared caption (same shape) |
| `caption_ig` | `null` | Optional Instagram-only shared caption (same shape; `description` is the Instagram caption text) |
| `post_times` | `["10:00","14:00","19:00"]` | Default daily posting times |
| `tz` | `"Asia/Kolkata"` | Timezone for `post_times` and display |
| `max_per_day_yt` | `6` | YouTube posts per day (default quota allows about 6 uploads) |
| `max_per_day_ig` | `10` | Instagram posts per day |
| `approval_timeout_min` | `10` | Countdown length; `0` = post without waiting |
| `retention_days` | `7` | Days to keep a file after a successful post |
| `storage_limit_gb` | `1` | Used for the storage card and the 80% warning |
| `poll_interval_min` | `5` | How often the watcher scans the bucket |
| `paused` | `false` | Global pause |
| `paused_yt`, `paused_ig` | `false` | Per-platform pause |
| `notify_enabled` | `true` | Email notifications on/off |
| `notify_email` | `""` | Where notifications go |
| `youtube_privacy` | `"public"` | `public`, `unlisted`, or `private` |

## 6. Video status model

Overall `status`: `queued` → `scheduled` → `awaiting_approval` → `uploading` → `posted` → `file_deleted`.
Other values: `invalid` (failed validation, never posted), `failed` (no platform job left pending and at least one failed).
Flags: `held` (admin hold, stops auto-approval), `keep` (exclude from cleanup).

Note: the earlier PRD had a separate `ready` state ("caption set"). Because a default caption always exists, a caption always resolves, so `ready` is merged into `queued`.

Per-platform status (`yt_status`, `ig_status`): `pending` → `uploading` → `posted` | `failed` | `skipped`.

Overall status derivation after any platform change:
- All enabled platforms `posted` or `skipped`, and at least one `posted` → `posted`, and set `delete_after = max(yt_posted_at, ig_posted_at) + retention_days`.
- No platform `pending`/`uploading`, and at least one `failed` → `failed`.
- Otherwise stays `uploading`.

## 7. Core logic (in `core/`)

### 7.1 Caption resolution (`captions.py`)

Resolve **at post time** (so edits made during the countdown are used), separately for each platform.

YouTube: use the first that exists of
1. the video's own `yt_title` / `yt_description` / `hashtags`,
2. `caption_yt` from settings,
3. `default_caption` from settings.

Instagram: use the first that exists of
1. the video's own `ig_caption` (+ `hashtags`),
2. `caption_ig` from settings,
3. `default_caption` from settings.

Record which level was used in `caption_source_yt` / `caption_source_ig`.

Rules:
- Placeholders in any caption text: `{filename}` (file name without extension) and `{number}` (the video's `seq`). Replace them before posting.
- YouTube: title max 100 characters (truncate at a word boundary), description gets `#Shorts` appended if missing, tags from hashtags without `#`. If the resolved title is empty, use the file name without extension as the title (never fail for a missing title).
- Instagram: caption max 2,200 characters; append hashtags as `#tag` words (up to 30 allowed, 3 to 5 recommended).
- A video is **never held or failed for lack of a caption**: the default caption is the last fallback.

### 7.2 Detection and validation (job: `watcher`, every `poll_interval_min`)

1. List objects under `STORAGE_INBOX_PREFIX` ending in `.mp4` (case-insensitive). Page through the listing so buckets with more than 100 objects are fully scanned.
2. For each key not already in `videos`: create a record (`status=queued`, `seq` from the sequence, `public_url` = the bucket's public object URL (`supabase.storage.from_(bucket).get_public_url(key)`), `queue_position = max+1`), and log `detected`. Conflicts on `file_key` are ignored.
3. Validate with `ffprobe` reading the public URL (`ffprobe -v error -show_streams -show_format -of json URL`). Require: container MP4, video codec `h264`, audio codec `aac` (audio stream present), height > width (vertical; 9:16 is the target, allow small tolerance), duration at most 90 seconds (Reels limit; YouTube Shorts allows up to 3 minutes but the stricter limit applies because every video goes to both platforms). File size must be at most `MAX_FILE_MB` (50 MB, the Supabase free plan per-file limit); larger files become `invalid` with a clear reason. Store `duration_s`, `width`, `height`, `size_bytes`.
4. On failure set `status=invalid` with `invalid_reason`, log it, and never post it.
5. Duplicate detection (should-have): if a `file_hash` match exists, mark the new record `invalid` with reason `duplicate`. Compute the hash only if cheap (the object's ETag from Supabase Storage metadata is acceptable as the hash).

### 7.3 Scheduling (job: `tick`, every 1 minute)

Each tick does these steps in order, inside a try/except so one failure does not stop the others. Use `now()` from an injectable application clock (not database time), so the fake-clock tests in Phase 3 work.

**a) Assign slots.** For every `queued` video (ordered by `queue_position`, then `created_at`) with no `scheduled_at`:
- If `post_now = true`: `scheduled_at = now`.
- Else if `custom_post_at` is set: `scheduled_at = custom_post_at`.
- Else: take the next free slot from `post_times` in the `tz` timezone, starting from now. One video per slot. A slot is free when no other video has `scheduled_at` equal to it. Continue into following days when slots run out.
- Set `status=scheduled`.

**b) Open the approval gate.** For every `scheduled` video with `scheduled_at <= now`:
- If `post_now = true` or `approval_timeout_min = 0`: approve immediately (`approved_by='admin'` for post-now, `'auto'` for timeout 0), go to step d.
- Else set `status=awaiting_approval`, `approval_due_at = scheduled_at + approval_timeout_min` minutes. The countdown starts at the scheduled time, not at detection. If the system was asleep and `scheduled_at` is far in the past, start the countdown from `now` instead so the admin still gets the full window.

**c) Auto-approve.** For every `awaiting_approval` video where `held = false` and `approval_due_at <= now`: set `approved_at = now`, `approved_by = 'auto'`. A held video stays in `awaiting_approval` until the admin releases it (release restarts nothing: after release it is approved immediately, `approved_by='admin'`).

**d) Dispatch.** For every approved video, set `status=uploading`; per-platform jobs are those with status `pending` and `*_next_attempt_at` null or in the past. Respect pauses (`paused`, `paused_yt`, `paused_ig`) and daily caps: if the platform's posts today (in `tz`) already reached `max_per_day_*`, set that platform's `*_next_attempt_at` to the first slot of the next day and leave the other platform alone.

The uploader (7.4) processes dispatchable platform jobs **one at a time** (a simple in-process lock) to stay within memory.

**Reordering.** The queue reorder action updates `queue_position` and clears `scheduled_at` (and sets `status=queued`) for all videos that are `scheduled` and have no `custom_post_at`, so slots are reassigned in the new order on the next tick. Do the whole reorder in one `transaction.atomic()` block.

### 7.4 Uploading (job: `uploader`, every 30 seconds)

Claim a platform job with an atomic update: `Video.objects.filter(id=id, yt_status='pending').update(yt_status='uploading')` and proceed only if it returns 1. Before uploading, if `yt_video_id` (or `ig_media_id`) is already set, mark posted and skip (idempotent).

On startup, reset any platform job stuck in `uploading` for longer than 30 minutes back to `pending` (a restart interrupted it), but only if `*_video_id` / `*_media_id` is empty, so a restart mid-upload never double-posts.

**YouTube** (`youtube.py`)
- OAuth 2.0 web flow, scope `https://www.googleapis.com/auth/youtube.upload`, `access_type=offline`, `prompt=consent`. Store the refresh token encrypted. Refresh access tokens automatically. Validate the OAuth `state` parameter against the session.
- Upload with `videos.insert`, `part=snippet,status`, resumable and chunked (for example 5 MiB chunks) reading from a streamed HTTP download of the public URL, not a full download. Body: `snippet.title`, `snippet.description`, `snippet.tags`, `snippet.categoryId="22"`, `status.privacyStatus` from `youtube_privacy`, `status.selfDeclaredMadeForKids=false`.
- On success: `yt_video_id`, `yt_url = https://youtube.com/shorts/{id}`, `yt_posted_at`, `yt_status=posted`.
- Note for the README: an unverified Google Cloud project forces uploads to private until the API compliance audit is completed.

**Instagram** (`instagram.py`)
- Graph API at `https://graph.facebook.com/{GRAPH_API_VERSION}`.
- `POST /{ig_user_id}/media` with `media_type=REELS`, `video_url=<public_url>`, `caption`, `share_to_feed=true` → `creation_id`.
- Poll `GET /{creation_id}?fields=status_code` every 10 seconds for up to 5 minutes until `FINISHED`. `ERROR` or timeout counts as a failure.
- `POST /{ig_user_id}/media_publish` with `creation_id` → `ig_media_id`. Then `GET /{media_id}?fields=permalink` → `ig_permalink`.
- Token: store the long-lived token encrypted with `expires_at`. The `token_refresh` job (daily) refreshes it when fewer than 10 days remain, using the refresh method that matches the token type, and logs `token_refreshed`. If refresh fails, set the account `status='error'`, log it, and send an email.
- The video file must stay in the public bucket and be publicly reachable until `media_publish` succeeds. Cleanup never touches non-posted videos, so this holds.

**Retries and errors**
- Up to 3 attempts per platform with backoff 5 min, 30 min, 2 h (`*_attempts`, `*_next_attempt_at`).
- Quota and rate-limit errors do **not** count as attempts: defer instead. For YouTube `quotaExceeded`, defer until the next quota reset (midnight Pacific Time); for Instagram rate limits, defer 1 hour.
- After the third failed attempt set `*_status=failed`, store `*_error`, log `failed`, send the failure email (7.6).
- YouTube and Instagram are independent: one failing never blocks the other.
- The admin can retry a failed platform from the dashboard (resets attempts and status to `pending`).

### 7.5 Cleanup (job: `cleanup`, daily at 03:00 in `tz`)

Delete the storage object for videos where `status='posted'`, `keep=false`, `file_deleted_at IS NULL`, `delete_after <= now`. Then set `file_deleted_at = now`, `status='file_deleted'`, log `deleted`. **Never delete** videos with status `queued`, `scheduled`, `awaiting_approval`, `uploading`, `failed`, or `invalid`. Database rows and history stay. The dashboard can also "Delete now" or "Keep" a video.

Same job, second step: prune `activity_log` rows older than 90 days to keep the free-plan database small.

### 7.6 Email notifications (`notifier.py`)

- Sent through SMTP using the `SMTP_*` environment variables, to `notify_email`, only when `notify_enabled` is true.
- One email per **final** result per platform: success, or failure after retries are exhausted. Not one per retry.
- Subject: `[Shorts] Posted to YouTube: <title>` or `[Shorts] FAILED on Instagram: <title>`.
- Body: video title and file name, platform, time, the post link (success) or the error text and attempt count (failure), and a link to the video page (`APP_PUBLIC_URL/videos/<id>/`).
- Send in a background thread with a short timeout. An SMTP failure must **not** break the upload flow: log `email_failed` and continue.
- The admin can turn notifications on/off and change the email address on the Settings page.

### 7.7 Storage numbers

- Used = sum of `size_bytes` for videos with `file_deleted_at IS NULL`.
- Limit = `storage_limit_gb`. Free = limit minus used. Warn in the UI above 80%.
- A `storage_check` job (hourly) lists the bucket and compares the total with the database sum; log a warning when they differ by more than 5%.
- The same hourly job logs a warning if the database size (`SELECT pg_database_size(current_database())`) exceeds 400 MB (80% of the free 500 MB database limit).

### 7.8 Pause and Hold

- `paused` stops all dispatching and auto-approval globally. `paused_yt` / `paused_ig` stop one platform. Countdown timers do not auto-approve while `paused` is true.
- `held` is per video and stops only that video's auto-approval.

## 8. Scheduler jobs (APScheduler inside the Django process)

| Job | Interval |
|-----|----------|
| `watcher` (detect + validate) | every `poll_interval_min` minutes (default 5) |
| `tick` (assign slots, open gate, auto-approve, dispatch) | every 1 minute |
| `uploader` (process pending platform jobs, one at a time) | every 30 seconds |
| `cleanup` (files + log pruning) | daily 03:00 in `tz` |
| `token_refresh` | daily |
| `storage_check` | hourly |

Rules:
- Start the scheduler in `CoreConfig.ready()` **only when** `RUN_SCHEDULER=1` and the process is the web server (skip for `migrate`, `collectstatic`, `makemigrations`, `test`, and shell commands). Guard against double start with a module-level flag. Use `BackgroundScheduler(daemon=True)`.
- `max_instances=1` and `coalesce=True` on every job. Wrap each job body in try/except, call `close_old_connections()` before and after, and log errors to `ActivityLog`.
- On startup (after the scheduler starts), run `tick` once immediately so overdue slots and expired countdowns catch up after a Render restart or sleep.
- Gunicorn must run with exactly one worker (`--workers 1 --threads 4`) so the scheduler is never duplicated. Document this in the README.

## 9. Routes (Django views)

All routes except `/health`, `/login`, and static files require a logged-in admin (enforced by `dashboard/middleware.py`; unauthenticated requests redirect to `/login`, HTMX requests get `HX-Redirect`). Every POST is CSRF-protected (HTMX sends the token via `hx-headers`). HTML pages return full templates; HTMX endpoints return partial templates; the few JSON endpoints are noted. Return clear error messages (shown as toasts).

| Method and path | Purpose |
|-----------------|---------|
| `GET /health` | Returns `{"ok": true}`. **Public, no auth, fast, no DB call needed.** Used by the keep-alive cron |
| `GET, POST /login` | Form with email and password; checks `ADMIN_EMAIL` and the bcrypt `ADMIN_PASSWORD_HASH`; starts a session. Rate-limit failed logins (for example 5 per 15 minutes per IP, cache-based) |
| `POST /logout` | Ends the session |
| `GET /` | Overview page |
| `GET /partials/summary` | HTMX fragment: overview cards (polled every 10 s) |
| `GET /videos` | List with filters `status`, `platform`, `from`, `to`, `q` (title search), pagination; HTMX fragment for the table body |
| `GET /videos/<id>/` | One video with its log entries |
| `POST /videos/<id>/edit` | Edit `yt_title`, `yt_description`, `ig_caption`, `hashtags`, `custom_post_at`, `keep` |
| `POST /videos/<id>/approve` | Approve now (`approved_by='admin'`) |
| `POST /videos/<id>/hold`, `/release` | Hold / release (release approves immediately) |
| `POST /videos/<id>/post-now` | Set `post_now=true`, approve and dispatch immediately |
| `POST /videos/<id>/retry` | Retry a failed platform (field `platform=yt\|ig`) |
| `POST /videos/<id>/skip` | Skip a platform (field `platform=yt\|ig`) |
| `POST /videos/<id>/delete` | Delete the stored file now (only if not currently uploading) and mark `file_deleted` |
| `POST /videos/bulk` | `ids[]` and `action` = `approve`, `retry`, `delete`, `reschedule` |
| `POST /videos/scan` | Run the watcher now |
| `GET /approvals` | Approvals page; `GET /partials/approvals` is the HTMX fragment polled every 10 s, including `seconds_remaining` per video |
| `POST /approvals/approve-all` | Approve every awaiting video |
| `GET /queue` | Ordered upcoming videos with scheduled times |
| `POST /queue/reorder` | `ids[]` in the new order (called by SortableJS) |
| `POST /queue/pause` | Toggle global or per-platform pause (`scope=all\|yt\|ig`) |
| `GET, POST /settings` | Read and update settings (validate: times as `HH:MM`, timezone name, positive numbers, valid email) |
| `GET /accounts` | Connection status, account names, token expiry, today's YouTube quota estimate (uploads today × 1,600 of 10,000) |
| `GET /accounts/youtube/connect` | Redirects to Google's OAuth consent URL |
| `GET /accounts/youtube/callback` | OAuth redirect target; stores tokens; redirects to `/settings` |
| `POST /accounts/instagram/token` | Fields `access_token`, `ig_user_id`; stores encrypted; fetches the account name |
| `POST /accounts/<platform>/test` | Verifies credentials (YouTube channel lookup; Instagram `GET /{ig_user_id}?fields=username`) |
| `POST /notifications/test` | Sends a test email to `notify_email` |
| `GET /logs` | Paginated activity log with filters |
| `POST /uploads/presign` | JSON: returns Supabase signed upload URLs (`create_signed_upload_url`) so the browser can upload many files straight to storage `inbox/` (the service role key never leaves the server) |

## 10. Dashboard (Django templates + HTMX)

The UI is server-rendered. `base.html` provides the layout (sidebar/top nav, toast area, CSRF header for HTMX). Polling uses `hx-trigger="every 10s"` on the approvals list and the overview cards. The countdown uses Alpine.js: each approval card receives `seconds_remaining` from the server and ticks down locally each second; every poll replaces the card with fresh server values so the countdown always matches the server. Toasts show success and errors (use HTMX response headers or `django.contrib.messages`). Responsive layout, loading states (`htmx-indicator`) and empty states on every list.

**Pages**

1. **Login**: email and password.
2. **Overview**
   - Cards: posted today / this week / this month / all time (total and per platform), in queue, awaiting approval, failed.
   - Storage card: used, free, percent of the limit, warning above 80%.
   - Next 5 scheduled posts (time and platform).
   - Platform health: YouTube and Instagram connection status, token expiry, today's YouTube quota use.
   - A banner if `default_caption` is empty, an account is disconnected, or notifications have no email.
3. **Approvals**: live list of videos awaiting approval: video preview (the public URL in a `<video>` tag), caption (as it will be posted, per platform), platforms, and a **countdown to auto-post**. Buttons: **Approve now**, **Edit caption**, **Hold** (or **Release** when held), **Skip YouTube**, **Skip Instagram**, plus **Approve all**.
4. **Videos**: table with thumbnail/preview, file name, title, YouTube status and Instagram status shown separately (with links to the live posts), scheduled time, posted time, size, delete date. Filters by status, platform, date range; search by title. Per-video actions: edit caption (own caption per platform or same for both), change post time, **Post now**, hold, retry, skip a platform, keep, delete. Bulk actions: approve, retry, delete, reschedule. A multi-file upload box sends files straight to Supabase Storage via the presigned URLs.
5. **Queue**: ordered queue with drag-and-drop reordering (SortableJS posts the new order to `/queue/reorder`), each row showing the scheduled time. Controls for pause/resume (global and per platform).
6. **Settings**: default caption, optional YouTube-only and Instagram-only captions (with a note on placeholders `{filename}` and `{number}` and the priority order: video's own → platform → default), default hashtags, default posting times (add/remove times), timezone, max posts per day per platform, approval countdown (default 10 minutes, 0 = no wait), retention days, storage limit, polling interval, YouTube privacy, **email notifications on/off switch and notification email address**, a "send test email" button, and the YouTube and Instagram connect/reconnect/test controls. SMTP credentials are **not** editable here (environment variables only).
7. **Logs**: activity log table with timestamps, event, platform, message, and error details.

Make it responsive, clean, and fast. Do not use any external JS build step: Tailwind is compiled once by the standalone CLI in the Dockerfile; HTMX, Alpine.js and SortableJS are loaded from a CDN with fixed versions (or vendored into `static/`).

## 11. Security requirements

- Single admin; login with a bcrypt password hash and a Django session (HTTP-only cookie, `SESSION_COOKIE_SECURE=True`, `CSRF_COOKIE_SECURE=True`, `SameSite=Lax`, session age from `SESSION_COOKIE_AGE_HOURS`). Rate-limit login attempts.
- OAuth tokens and the Instagram token are stored **encrypted** (Fernet) and are never rendered in templates or written to logs.
- The Supabase service role key is used only on the server. HTTPS only (`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, `SECURE_SSL_REDIRECT=True` in production, HSTS on). Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` from the environment.
- The database connection string (which contains the Supabase database password) and SMTP credentials live in environment variables only.
- Row Level Security is enabled on every table with no policies, so the Supabase anon and authenticated keys (and the auto-generated REST API) cannot read or write any data. Only the app's direct Postgres connection can.
- Use the Django ORM and forms for all queries and input validation; never build SQL by string concatenation. Use Django's auto-escaping in templates; never mark user-entered captions as `safe`.
- Django's built-in `/admin/` site is **not enabled**.
- The public `/health` endpoint must not expose any data.

## 12. Deployment (one Render service)

**Render free tier, Docker, one web service**
- `Dockerfile`: `python:3.12-slim`, install `ffmpeg`, install requirements, download the Tailwind standalone CLI and build the CSS, run `collectstatic`. `entrypoint.sh` runs `python manage.py migrate --noinput` then `exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`.
- Set all environment variables in the Render dashboard. Add `render.yaml` (optional) describing the single web service.
- **Keep-alive:** document, in the README, how to create a cron job (for example on cron-job.org, or any scheduler) that calls `GET https://<app>/health` **every 10 minutes**, because Render's free web service sleeps when idle (a sleeping service also stops the scheduler).
- Because the service can restart, startup must be safe: migrations are idempotent, the scheduler starts, `tick` runs once.
- After the first deploy, put the Render URL into `APP_PUBLIC_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and the Google OAuth redirect URI.

**One-time setup steps to include in the README**
1. In Supabase, create **one** project (or reuse one). It provides both the database and storage.
   - Storage: create a **public** bucket named `shorts` with an `inbox/` folder. Free plan limits: 1 GB storage, 5 GB egress per month, 50 MB per file.
   - Database: go to Project Settings > Database > Connection string, choose **Session pooler**, and copy it into `DATABASE_URL` (append `?sslmode=require`). Explain in the README why the direct connection and the transaction pooler are not used.
   - Copy the project URL and the **service role** key into `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
   - Free projects pause after 7 days of inactivity; the app's per-minute database queries should count as activity, but verify this after setup.
   - Tables are created automatically on first start by `migrate`.
2. Create a Google Cloud project, enable YouTube Data API v3, create an OAuth client (web), set the redirect URI to `<app>/accounts/youtube/callback`, then connect from the Settings page. Mention the compliance audit needed for public uploads.
3. Create a Meta developer app, link an Instagram Professional (Creator or Business) account to a Facebook Page, generate a long-lived token, and paste the token and Instagram user ID into the Settings page.
4. Set SMTP credentials, then use the Settings page "send test email" button.
5. Generate `ADMIN_PASSWORD_HASH`, `DJANGO_SECRET_KEY`, `ENCRYPTION_KEY` (provide `scripts/gen_secrets.py`).
6. Create the Render web service from the repo (Docker), set the environment variables, deploy, then create the keep-alive cron job.

## 13. Platform limits to respect

- YouTube default quota: 10,000 units per day; one upload costs about 1,600, so about 6 uploads per day. A batch of 20 videos therefore spreads over 3 to 4 days. The scheduler and the daily cap must handle this without marking videos failed.
- Instagram: about 50 published posts per 24 hours; Reels format MP4, H.264, AAC, 9:16, up to 90 seconds; the video URL must be public and directly downloadable.
- Keep daily posts spread across `post_times`. Do not burst-post.

## 14. Build phases and acceptance checks

**Phase 1: Foundation**
Django project, env-driven settings, models and migrations on Supabase Postgres (session pooler), settings store with defaults, single-admin login with sessions and rate limiting, login-required middleware, `/health`, encryption helpers, Dockerfile and `entrypoint.sh`, base template with Tailwind/HTMX/Alpine.
Check: app starts; `migrate` creates tables, sequence, triggers and RLS (running it again does not error); `updated_at` changes on update; login works and a wrong password is rejected and rate-limited; every page redirects to `/login` when logged out; `/health` returns 200 without auth; the Supabase anon key cannot read any table through the REST API.

**Phase 2: Storage ingestion**
`storage.py`, watcher, ffprobe validation, Videos list page, manual scan button.
Check: three test MP4s in `inbox/` appear as `queued`; a wrong-format or horizontal file becomes `invalid` with a reason; no duplicates on repeated or concurrent scans.

**Phase 3: Captions and scheduling**
Caption resolution (7.1), slot assignment, approval gate, auto-approve, hold/release, post-now, reorder, pause.
Check (unit tests with a fake clock): 10 videos get consecutive slots across days; a video with no caption uses the default; countdown starts at `scheduled_at`; an untouched video auto-approves exactly after the timeout; a held video does not; post-now skips the wait.

**Phase 4: Platform uploads**
YouTube OAuth and resumable upload; Instagram Reels publish with polling; retries and deferral; per-platform status; daily caps.
Check: with mocked platform clients, a success path, a failure with retries (backoff 5 min, 30 min, 2 h), a quota error that defers without counting, and the independence of the two platforms all behave as specified; a restart mid-upload never double-posts; two simultaneous claims of the same job result in exactly one upload.

**Phase 5: Notifications and cleanup**
SMTP notifier with on/off and email address, cleanup job (files and log pruning), storage and database size stats, token refresh job.
Check: one email per final result; no email when notifications are off; SMTP failure does not break posting; cleanup deletes only files 7 days after a successful post and never touches queued, uploading, or failed videos.

**Phase 6: Dashboard pages**
All pages in section 10 with HTMX polling, Alpine countdowns, SortableJS reordering, toasts, upload box, and responsive layout.
Check: approving, holding, editing, retrying, reordering, changing settings, uploading files, and connecting accounts all work from the UI; the countdown matches the server after each poll.

**Phase 7: Deploy and docs**
`render.yaml`, README with the setup steps in section 12, `.env.example`, secret generator script.
Check: a fresh clone can be deployed to a single Render service by following the README only, and the scheduler runs exactly once (verify there is no duplicated `tick` log entry per minute).

## 15. Definition of done

- Dropping 10 to 20 MP4s in `inbox/` results, without any further action, in posts on YouTube and Instagram at the default times, each after the approval countdown, unless the admin intervenes.
- No duplicate posts, ever. A video with no caption of its own posts with the default caption.
- Every final success and failure produces one email (when enabled).
- Files are removed from storage 7 days after a successful post, and only then.
- The dashboard (same Render service, same URL) shows posted counts, queue, approvals with countdown, storage used and free, account health, and logs.
- The system survives a Render sleep or restart without losing or duplicating work.
- Secrets never appear in code, logs, templates, or responses. The whole project runs as one Render service plus one Supabase project, and no table is reachable through Supabase's public API.

## 16. Out of scope for v1

AI-generated captions, multiple users, other platforms (TikTok, Facebook, X), analytics (views and likes), thumbnail selection, and the shorts generator itself.