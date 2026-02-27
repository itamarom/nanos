"""
Celery application configuration for the nanos worker.

Creates the Celery app, configures broker/backend via REDIS_URL,
and loads dynamic beat schedules from the database on worker startup.
"""

import os
import sys

# Ensure shared module is importable (worker/ is copied to /app/, shared/ to /app/shared/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from celery import Celery
from celery.signals import worker_ready

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery("nanos")

app.conf.update(
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    imports=["tasks"],
)


@worker_ready.connect
def on_worker_ready(**kwargs):
    """Load dynamic beat schedules from the database when the worker starts."""
    from scheduler import load_schedules

    load_schedules()


# Also load schedules at import time so celery beat picks them up.
from scheduler import load_schedules as _load
_load()
