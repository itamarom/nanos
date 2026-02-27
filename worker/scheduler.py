"""
Dynamic schedule loader for Celery Beat.

Reads all active nanos that have a schedule from the database and
registers them with Celery Beat so they execute on the configured cadence.

Supported schedule formats:
- Standard 5-field cron: ``"*/5 * * * *"``
- Interval shorthand: ``"20s"``, ``"5m"``, ``"1h"``
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from celery.schedules import crontab, schedule

from shared.database import SyncSessionLocal
from shared.models import Nano


def load_schedules():
    """
    Load dynamic beat schedules from the database.

    Queries all active nanos that have a non-null ``schedule`` field,
    parses each into a Celery schedule object, and updates
    ``celery_app.conf.beat_schedule``.

    This function is designed to be called on worker startup via the
    ``worker_ready`` signal.
    """
    from celery_app import app

    logger = logging.getLogger(__name__)
    session = SyncSessionLocal()
    try:
        try:
            nanos = (
                session.query(Nano)
                .filter(Nano.is_active.is_(True), Nano.schedule.isnot(None))
                .all()
            )
        except Exception:
            logger.warning("Could not load schedules (tables may not exist yet)")
            return

        beat_schedule = {}

        for nano in nanos:
            if not nano.schedule:
                continue
            schedule_str = nano.schedule.strip()
            if not schedule_str:
                continue

            sched = _parse_schedule(schedule_str)
            if sched is None:
                continue

            task_name = f"nano-{nano.name}"
            beat_schedule[task_name] = {
                "task": "tasks.run_nano_task",
                "schedule": sched,
                "args": [str(nano.id), "schedule"],
            }

        app.conf.beat_schedule = beat_schedule

    finally:
        session.close()


_INTERVAL_RE = re.compile(r"^(\d+)\s*(s|m|h)$")


def _parse_schedule(schedule_str: str) -> schedule | crontab | None:  # type: ignore[no-any-unimported]
    """Parse a schedule string into a Celery schedule object.

    Supports:
    - Interval shorthand: ``"20s"``, ``"5m"``, ``"1h"``
    - Standard 5-field cron: ``"*/5 * * * *"``

    Returns a Celery ``schedule`` or ``crontab``, or ``None`` on failure.
    """
    # Try interval shorthand first
    m = _INTERVAL_RE.match(schedule_str)
    if m:
        value, unit = int(m.group(1)), m.group(2)
        if unit == "s":
            return schedule(run_every=timedelta(seconds=value))
        elif unit == "m":
            return schedule(run_every=timedelta(minutes=value))
        elif unit == "h":
            return schedule(run_every=timedelta(hours=value))

    # Fall back to cron (5 fields, or 6 fields with timezone)
    parts = schedule_str.split()
    if len(parts) not in (5, 6):
        return None

    tz = None
    if len(parts) == 6:
        try:
            tz = ZoneInfo(parts[5])
        except (ZoneInfoNotFoundError, KeyError):
            pass  # ignore invalid timezone, use default

    try:
        kwargs: dict[str, str | ZoneInfo] = {
            "minute": parts[0],
            "hour": parts[1],
            "day_of_month": parts[2],
            "month_of_year": parts[3],
            "day_of_week": parts[4],
        }
        if tz is not None:
            kwargs["tz"] = tz
        return crontab(**kwargs)
    except (ValueError, TypeError):
        return None
