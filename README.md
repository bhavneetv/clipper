# Shorts Auto-Poster

An automated, single-admin platform built with **Django 5**, **Supabase Postgres & Storage**, and **APScheduler**, designed to run completely on **one Render free-tier web service**.

The system automatically detects finished short videos (`.mp4`) dropped into a Supabase Storage bucket, schedules them into daily slots, provides an approval gate with live countdown (with automatic posting fallback), publishes independently to **YouTube Shorts** and **Instagram Reels**, sends email notifications, and automatically deletes files 7 days after publication.

---

## 1. Architecture & Design

- **All-in-One Service:** Single Django application hosting backend logic, scheduler, and the admin dashboard (Django templates + HTMX + Alpine.js + Tailwind CSS). No React, no external node server, no CORS.
- **Single Scheduler Process:** APScheduler (`BackgroundScheduler`) runs inside the Django process. Gunicorn runs with `--workers 1 --threads 4` so scheduler jobs run exactly once and state is never duplicated.
- **Memory Efficiency (512 MB Render Free Tier):** Videos are never loaded entirely into memory. Downloads and uploads stream in 5 MiB chunks.
- **State & Persistence:** Render local disk is treated as ephemeral. All metadata and logs persist in Supabase Postgres; video files reside in Supabase Storage.
- **Security & Row Level Security (RLS):** All tables have RLS enabled with NO policies. Supabase's anon and authenticated REST APIs cannot read or write data. Django connects via the internal Postgres role through the Session pooler, which bypasses RLS. OAuth tokens are encrypted using **Fernet (AES-128-CBC)**.

---

## 2. Prerequisites

1. **Supabase Account & Project:** Provides Postgres database and public storage.
2. **Google Cloud Project:** With YouTube Data API v3 enabled and OAuth 2.0 Web Client credentials.
3. **Meta Developer App:** With Instagram Graph API enabled and linked to an Instagram Professional account.
4. **SMTP Service:** Any SMTP server (e.g., Gmail, SendGrid, Resend, Brevo).
5. **Render Account:** Free-tier web service using Docker.

---

## 3. Step-by-Step Setup Guide

### Step 1: Supabase Setup (Database & Storage)

1. Log into your [Supabase Dashboard](https://app.supabase.com) and create a project (e.g., in region `ap-southeast-2` or closest to you).
2. **Create Storage Bucket:**
   - Go to **Storage** > **New bucket**.
   - Name: `shorts`.
   - **Public bucket:** Enable toggle (**must be Public** so Instagram and YouTube stream endpoints can download video URLs).
   - Set max file size limit to `50 MB`.
   - Inside the bucket, create an `inbox/` folder.
3. **Database Connection String (Session Pooler):**
   - Go to **Project Settings** > **Database** > **Connection string**.
   - Select **Session pooler** (Port `5432`).
   - Format:
     ```
     postgresql://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres?sslmode=require
     ```
   - **Why Session Pooler?** Render's free tier is IPv4-only, whereas Supabase's direct connection host is IPv6-only. The Session Pooler provides an IPv4 endpoint compatible with Django's prepared statements and advisory locks (unlike the Transaction pooler on port 6543 which breaks prepared statements).
4. **API Keys:**
   - Go to **Project Settings** > **API**.
   - Copy the **Project URL** (`https://<project-ref>.supabase.co`).
   - Copy the **`service_role` secret key** (used only on the server, never sent to browsers).

---

### Step 2: Google Cloud & YouTube Setup

1. In the [Google Cloud Console](https://console.cloud.google.com):
   - Create a new project.
   - Go to **APIs & Services** > **Library** and enable **YouTube Data API v3**.
2. **Configure OAuth Consent Screen:**
   - User Type: **External**.
   - Add scope: `https://www.googleapis.com/auth/youtube.upload`.
   - Add your Google account under **Test users**.
3. **Create Credentials:**
   - Go to **Credentials** > **Create Credentials** > **OAuth Client ID**.
   - Application type: **Web application**.
   - Authorized redirect URI:
     `https://<your-render-app>.onrender.com/accounts/youtube/callback`
     *(For local testing: `http://localhost:8000/accounts/youtube/callback`)*.
   - Copy `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
   - *Note on Public Uploads:* Google requires an API compliance audit for unverified projects to upload public videos. Until verified, uploads default to private/unlisted.

---

### Step 3: Meta & Instagram Setup

1. In the [Meta for Developers](https://developers.facebook.com) portal:
   - Create an app of type **Business**.
   - Add the **Instagram Graph API** product.
2. Link an **Instagram Professional (Creator or Business)** account to a Facebook Page.
3. In Graph API Explorer, generate a **User Token** with `instagram_basic`, `instagram_content_publish`, and `pages_show_list` permissions.
4. Exchange for a long-lived 60-day token.
5. Note your Instagram Business Account ID (`ig_user_id`). You can connect these in the **Accounts** tab of the dashboard.

---

### Step 4: Generate Application Secrets

Run the provided secrets generator script in Python:

```bash
python scripts/gen_secrets.py
```

This will output:
- `DJANGO_SECRET_KEY`: High-entropy random key for sessions and CSRF protection.
- `ENCRYPTION_KEY`: 32 URL-safe base64-encoded bytes for Fernet token encryption.
- `ADMIN_PASSWORD_HASH`: Bcrypt hash for single admin authentication.

---

### Step 5: Environment Variables Reference

Copy `.env.example` to `.env` for local development or enter them into the Render Dashboard:

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Random secret key generated by `gen_secrets.py` |
| `DJANGO_DEBUG` | `false` in production, `true` for local development |
| `APP_PUBLIC_URL` | Public URL of application (e.g. `https://shorts-app.onrender.com`) |
| `ALLOWED_HOSTS` | Comma-separated hosts (e.g. `shorts-app.onrender.com`) |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins (e.g. `https://shorts-app.onrender.com`) |
| `RUN_SCHEDULER` | `1` to run background scheduler jobs; `0` for tests/commands |
| `SESSION_COOKIE_AGE_HOURS` | Session expiry time in hours (default: `24`) |
| `DATABASE_URL` | Supabase Session pooler connection string with `?sslmode=require` |
| `SUPABASE_URL` | Supabase Project URL (`https://<ref>.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service_role secret key |
| `SUPABASE_BUCKET` | Public bucket name (default: `shorts`) |
| `STORAGE_INBOX_PREFIX` | Inbox prefix folder (default: `inbox/`) |
| `MAX_FILE_MB` | Maximum video size in MB (default: `50`) |
| `ADMIN_EMAIL` | Email for single admin login |
| `ADMIN_PASSWORD_HASH` | Bcrypt hash generated by `gen_secrets.py` |
| `ENCRYPTION_KEY` | Fernet key generated by `gen_secrets.py` |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `GOOGLE_REDIRECT_URI` | `${APP_PUBLIC_URL}/accounts/youtube/callback` |
| `META_APP_ID` | Meta App ID |
| `META_APP_SECRET` | Meta App Secret |
| `GRAPH_API_VERSION` | Meta Graph API Version (e.g. `v21.0`) |
| `SMTP_HOST` | SMTP server host (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (e.g. `587`) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password / app password |
| `SMTP_FROM` | Sender email address |
| `SMTP_STARTTLS` | `true` for TLS |

---

### Step 6: Deploy to Render (One Web Service)

1. Push your repository to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com):
   - Click **New** > **Web Service**.
   - Select your repository.
   - Choose **Docker** environment.
   - Plan: **Free**.
   - In **Environment Variables**, enter all the variables from the table above.
3. Click **Deploy Web Service**.
4. Render builds the Docker container (which installs ffmpeg, compiles Tailwind CSS, and runs `collectstatic`).
5. `entrypoint.sh` automatically runs database migrations (`python manage.py migrate --noinput`), creating all tables, sequences, triggers, and RLS policies idempotently, then boots Gunicorn with 1 worker and 4 threads.

---

### Step 7: Keep-Alive Cron Setup (Render Sleep Prevention)

Render free web services spin down after 15 minutes of inactivity. When the service sleeps, the APScheduler also pauses!

To ensure 24/7 autonomous scheduling:
1. Go to any free web cron service such as [cron-job.org](https://cron-job.org) or [UptimeRobot](https://uptimerobot.com).
2. Create a monitor or recurring job:
   - **URL:** `GET https://<your-render-app>.onrender.com/health`
   - **Interval:** **Every 10 minutes**
3. The `/health` endpoint is lightweight, public, requires no database query, and returns `{"ok": true}`, keeping your Render web service awake without consuming database quotas.

---

## 4. Operating the System

1. **Ingestion:**
   - Drop 10 to 20 MP4 short videos into the Supabase bucket under `inbox/` (or use the multi-file drag-and-drop uploader in the **Videos** tab).
   - The watcher detects new files, validates them with `ffprobe` (MP4, H.264, AAC, 9:16 vertical, duration <= 90s, size <= 50 MB), prevents duplicates via ETag hashing, and assigns queue numbers.
2. **Scheduling:**
   - Every minute, the scheduler assigns videos to daily slots (default: 10:00, 14:00, 19:00 in your configured timezone).
   - Drag and drop queue order anytime in the **Queue** tab (powered by SortableJS); slots automatically re-assign on the next tick.
3. **Approval Gate & Countdown:**
   - When a video's scheduled time begins, it appears in the **Approvals** tab with a countdown timer (default 10 minutes).
   - The admin can edit captions, approve now, hold, or skip platforms.
   - **If no action is taken, the video automatically posts when the countdown hits 00:00.**
4. **Publishing & Retries:**
   - Posts independently to YouTube and Instagram.
   - Quota exceeded errors automatically defer without burning retry attempts.
   - Other failures retry with backoff (5m, 30m, 2h).
5. **Retention Cleanup:**
   - Every morning at 03:00 local time, successfully posted videos older than 7 days have their source files automatically removed from Supabase storage (unless marked with `keep`).
   - Activity logs older than 90 days are pruned automatically.

---

## 5. Local Testing

To run the complete automated test suite locally:

```bash
# Run all tests across foundation, storage, scheduling, uploads, notifications, and UI
python manage.py test
```
