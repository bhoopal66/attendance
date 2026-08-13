from django.apps import AppConfig


class PayrollConfig(AppConfig):
    name = 'payroll'

    def ready(self):
        # Phase 13 — register DeductionEntry audit signal fallback.
        # Import inside ready() per Django convention (avoids AppRegistryNotReady).
        from . import signals  # noqa: F401
