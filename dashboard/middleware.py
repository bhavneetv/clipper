from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from core.settings_store import get_setting

ERROR_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error</title>
    <style>
        body {
            background-color: #0b0f19;
            color: #e2e8f0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }
        .box {
            text-align: center;
            max-width: 440px;
            padding: 32px 24px;
        }
        h1 {
            font-size: 24px;
            font-weight: 700;
            color: #f1f5f9;
            margin: 0 0 10px 0;
            letter-spacing: -0.025em;
        }
        p {
            font-size: 14px;
            color: #94a3b8;
            line-height: 1.6;
            margin: 0;
        }
    </style>
</head>
<body>
    <div class="box">
        <h1>Error happened</h1>
        <p>An unexpected error occurred while processing your request. Please try again later.</p>
    </div>
</body>
</html>"""


class LoginRequiredMiddleware:
    """
    Middleware handling:
    1. System Shutdown / Lock Mode: When enabled, all non-superadmin visitors see 'Error happened'.
    2. Route Access Control: Restricts /accounts to Super Admin (super@gmail.com).
    3. Authentication: Requires login for all dashboard endpoints except health/static.
    """

    EXEMPT_PATHS = {
        "/health",
        "/health/",
        "/login",
        "/login/",
        "/api/cron/tick",
        "/api/cron/tick/",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Always exempt static assets and system health checks
        if path.startswith("/static/") or path.rstrip("/") in ("/health", "/api/cron/tick"):
            return self.get_response(request)

        # 1. Shutdown / Emergency Lock enforcement
        shutdown_enabled = bool(get_setting("shutdown_enabled", False))
        if shutdown_enabled:
            # Check for emergency bypass key via query param: ?unlock=admin123456
            unlock_key = get_setting("shutdown_unlock_key", "admin123456")
            if request.GET.get("unlock") == unlock_key or request.GET.get("key") == unlock_key:
                request.session["shutdown_bypass"] = True

            is_super = request.session.get("user_role") == "super"
            has_bypass = request.session.get("shutdown_bypass") is True

            # If not superadmin or unlocked, return clean generic error
            if not is_super and not has_bypass:
                return HttpResponse(ERROR_PAGE_HTML, status=500, content_type="text/html; charset=utf-8")

        # 2. Authentication check
        if not request.session.get("is_authenticated"):
            if path in self.EXEMPT_PATHS or path.rstrip("/") in ("/login", "/health", "/api/cron/tick"):
                return self.get_response(request)
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=200)
                response["HX-Redirect"] = "/login"
                return response
            return HttpResponseRedirect("/login")

        # 3. Super Admin role restriction for Accounts
        if path.startswith("/accounts"):
            if request.session.get("user_role") != "super":
                messages.error(request, "Access restricted. Connected Accounts is only available to Super Admin (super@gmail.com).")
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse(status=200)
                    response["HX-Redirect"] = "/"
                    return response
                return HttpResponseRedirect("/")

        return self.get_response(request)
