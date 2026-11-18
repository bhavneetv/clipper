"""
Views for OAuth account connections (YouTube, Instagram) and verification testing.
"""
import json
import secrets
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
import requests
from core.facebook import get_valid_facebook_credentials, save_facebook_credentials
from core.instagram import get_graph_api_base, get_valid_instagram_access_token, save_instagram_credentials
from core.models import Account, ActivityLog, Video
from core.scheduling import get_now, get_tz
from core.youtube import exchange_youtube_code, get_valid_youtube_access_token, get_youtube_oauth_url


@require_GET
def accounts_view(request):
    """Render connected accounts, health status, and quota usage."""
    yt_acc = Account.objects.filter(platform="youtube").first()
    ig_acc = Account.objects.filter(platform="instagram").first()
    fb_acc = Account.objects.filter(platform="facebook").first()

    now = get_now()
    tz = get_tz()
    today_date = now.astimezone(tz).date()

    yt_today_count = Video.objects.filter(yt_status="posted", yt_posted_at__date=today_date).count()
    yt_quota_used = yt_today_count * 1600
    yt_quota_max = 10000

    fb_today_count = Video.objects.filter(fb_status="posted", fb_posted_at__date=today_date).count()

    context = {
        "yt_acc": yt_acc,
        "ig_acc": ig_acc,
        "fb_acc": fb_acc,
        "yt_quota_used": yt_quota_used,
        "yt_quota_max": yt_quota_max,
        "yt_today_count": yt_today_count,
        "fb_today_count": fb_today_count,
    }
    return render(request, "dashboard/accounts.html", context)


@require_GET
def youtube_connect_view(request):
    """Generate state, save in session, and redirect to Google OAuth."""
    state = secrets.token_urlsafe(32)
    request.session["youtube_oauth_state"] = state
    oauth_url = get_youtube_oauth_url(state)
    return redirect(oauth_url)


@require_GET
def youtube_callback_view(request):
    """Handle Google OAuth callback and token exchange."""
    saved_state = request.session.get("youtube_oauth_state")
    returned_state = request.GET.get("state")
    code = request.GET.get("code")

    if not saved_state or saved_state != returned_state:
        return render(request, "dashboard/accounts.html", {
            "error": "OAuth state mismatch. Security validation failed."
        })

    if not code:
        err = request.GET.get("error", "Unknown error")
        return render(request, "dashboard/accounts.html", {
            "error": f"Google authorization rejected: {err}"
        })

    success, err_msg = exchange_youtube_code(code)
    if not success:
        return render(request, "dashboard/accounts.html", {
            "error": f"Token exchange failed: {err_msg}"
        })

    return redirect("/accounts")


@require_POST
def instagram_token_view(request):
    """Save manual Instagram long-lived access token and user ID."""
    access_token = request.POST.get("access_token", "").strip()
    ig_user_id = request.POST.get("ig_user_id", "").strip()

    if not access_token or not ig_user_id:
        messages.error(request, "Both Access Token and Instagram User ID are required.")
        return redirect("/accounts")

    success, err_msg = save_instagram_credentials(access_token, ig_user_id)
    if not success:
        messages.error(request, err_msg or "Failed to connect Instagram.")
    else:
        messages.success(request, "Instagram account connected successfully!")

    return redirect("/accounts")


@require_POST
def facebook_token_view(request):
    """Save manual Facebook Page ID and Page Access Token."""
    access_token = request.POST.get("access_token", "").strip()
    page_id = request.POST.get("page_id", "").strip()

    if not access_token:
        messages.error(request, "Facebook Access Token is required.")
        return redirect("/accounts")

    success, err_msg = save_facebook_credentials(access_token, page_id)
    if not success:
        messages.error(request, err_msg or "Failed to connect Facebook Page.")
    else:
        messages.success(request, "Facebook Page (and connected Instagram Reels) linked successfully!")

    return redirect("/accounts")


@require_POST
def account_test_view(request, platform):
    """Test platform credentials live."""
    platform = platform.lower()
    if platform == "youtube":
        token = get_valid_youtube_access_token()
        if not token:
            res = HttpResponse(status=200)
            res["HX-Trigger"] = json.dumps({"showMessage": {"message": "YouTube disconnected or token invalid.", "type": "error"}})
            return res

        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if r.status_code == 200 and r.json().get("items"):
                title = r.json()["items"][0]["snippet"]["title"]
                acc = Account.objects.filter(platform="youtube").first()
                if acc and not acc.account_name:
                    acc.account_name = title
                    acc.save(update_fields=["account_name"])
                res = HttpResponse(status=200)
                res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"YouTube Connected: Channel '{title}' verified!", "type": "success"}})
                return res

            # Fallback: If only upload scope is granted, channels.list returns 403. Check tokeninfo instead.
            tinfo = requests.get(
                f"https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={token}",
                timeout=10,
            )
            if tinfo.status_code == 200:
                scope = tinfo.json().get("scope", "")
                if "youtube" in scope:
                    acc = Account.objects.filter(platform="youtube").first()
                    if acc and not acc.account_name:
                        acc.account_name = "Connected Channel"
                        acc.save(update_fields=["account_name"])
                    acc_label = (acc.account_name if acc and acc.account_name else "YouTube Channel")
                    res = HttpResponse(status=200)
                    res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"YouTube Connected: {acc_label} permissions verified successfully!", "type": "success"}})
                    return res
        except Exception as e:
            pass

        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": "YouTube test failed: unable to verify channel.", "type": "error"}})
        return res

    elif platform == "instagram":
        token, ig_user_id = get_valid_instagram_access_token()
        if not token or not ig_user_id:
            res = HttpResponse(status=200)
            res["HX-Trigger"] = json.dumps({"showMessage": {"message": "Instagram disconnected or token missing.", "type": "error"}})
            return res

        base = get_graph_api_base()
        try:
            r = requests.get(
                f"{base}/{ig_user_id}",
                params={"fields": "username,name", "access_token": token},
                timeout=10,
            )
            if r.status_code == 200:
                uname = r.json().get("username") or r.json().get("name")
                res = HttpResponse(status=200)
                res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Instagram Connected: @{uname} verified!", "type": "success"}})
                return res
        except Exception as e:
            pass

        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": "Instagram test failed: unable to verify user.", "type": "error"}})
        return res

    elif platform == "facebook":
        token, page_id = get_valid_facebook_credentials()
        if not token or not page_id:
            res = HttpResponse(status=200)
            res["HX-Trigger"] = json.dumps({"showMessage": {"message": "Facebook Page disconnected or token missing.", "type": "error"}})
            return res

        base = get_graph_api_base()
        try:
            r = requests.get(
                f"{base}/{page_id}",
                params={"fields": "name", "access_token": token},
                timeout=10,
            )
            if r.status_code == 200:
                pname = r.json().get("name") or page_id
                res = HttpResponse(status=200)
                res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Facebook Connected: Page '{pname}' verified!", "type": "success"}})
                return res
        except Exception as e:
            pass

        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": "Facebook test failed: unable to verify page.", "type": "error"}})
        return res

    return redirect("/accounts")
