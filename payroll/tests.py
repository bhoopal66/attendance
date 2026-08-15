"""Payroll test entry point.

Django's default runner discovers `test*.py` inside the app, so the suites
below are picked up by `manage.py test payroll` without this file. The import
is here so that `manage.py test payroll.tests` — which people type out of
habit — runs them too rather than reporting zero tests and looking green.

    python manage.py test payroll                       # everything
    python manage.py test payroll.tests_sunday_entitlement   # just the Sunday engine

The Sunday suite is `SimpleTestCase` with `databases = []`: the engine is a
pure function, so it needs no database and runs in milliseconds.
"""

from .tests_sunday_entitlement import *  # noqa: F401,F403
