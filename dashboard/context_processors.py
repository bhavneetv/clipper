from core.settings_store import get_setting


def system_status(request):
    """Provides shutdown status and role helpers to all templates."""
    return {
        "shutdown_active": bool(get_setting("shutdown_enabled", False)),
        "is_superadmin": request.session.get("user_role") == "super",
        "current_user_email": request.session.get("admin_email", ""),
        "current_user_role": request.session.get("user_role", "admin"),
    }
