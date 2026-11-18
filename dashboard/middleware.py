from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse


class LoginRequiredMiddleware:
    """
    Middleware requiring admin authentication for all routes
    except /health, /login, and static files.
    Redirects unauthenticated requests to /login (or HX-Redirect for HTMX).
    """

    EXEMPT_PATHS = {
        "/health",
        "/login",
        "/api/cron/tick",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Exempt static assets and specific endpoints
        if path.startswith("/static/") or path in self.EXEMPT_PATHS:
            return self.get_response(request)

        # Check session authentication
        if not request.session.get("is_authenticated"):
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=200)
                response["HX-Redirect"] = "/login"
                return response
            return HttpResponseRedirect("/login")

        return self.get_response(request)
