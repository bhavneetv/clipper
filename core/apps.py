import os
import sys
from django.apps import AppConfig

_SCHEDULER_STARTED = False


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        global _SCHEDULER_STARTED
        if _SCHEDULER_STARTED:
            return

        # Skip during management commands
        skip_cmds = {
            "migrate",
            "makemigrations",
            "collectstatic",
            "test",
            "shell",
            "createsuperuser",
            "check",
            "sqlmigrate",
            "showmigrations",
            "loaddata",
            "dumpdata",
        }
        if any(cmd in sys.argv for cmd in skip_cmds):
            return

        run_scheduler = os.environ.get("RUN_SCHEDULER", "1").lower() in ("1", "true", "yes")
        if not run_scheduler:
            return

        # Only run scheduler in gunicorn or runserver
        # Guard against duplicate start in Django runserver reloader
        if "runserver" in sys.argv:
            if "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
                return

        try:
            from core.jobs import start_scheduler
            start_scheduler()
            _SCHEDULER_STARTED = True
        except Exception as e:
            # Avoid crashing during migrations or app setup if tables aren't ready
            pass
