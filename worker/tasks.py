"""
Celery task definitions for executing nanos.

Provides the main `run_nano_task` that looks up a nano from the database,
creates a RunLog entry, executes the nano script via the runner, and
records the result.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from celery import shared_task, Task
from sqlalchemy.orm import Session

from shared.database import SyncSessionLocal
from shared.models import Nano, NanoState, RunLog, PendingApproval
from runner import execute_nano


def _snapshot_state(session: Session, nano_id: uuid.UUID) -> str | None:
    """Return a JSON string snapshot of all state entries for a nano."""
    rows = (
        session.query(NanoState)
        .filter(NanoState.nano_id == nano_id)
        .order_by(NanoState.key)
        .all()
    )
    if not rows:
        return None
    state = {}
    for row in rows:
        try:
            state[row.key] = {"value": json.loads(row.value), "type": row.value_type}
        except (json.JSONDecodeError, TypeError):
            state[row.key] = {"value": row.value, "type": row.value_type}
    return json.dumps(state)


@shared_task(bind=True, soft_time_limit=300, name="tasks.run_nano_task")
def run_nano_task(self: Task, nano_id: str, trigger: str, run_log_id: str | None = None, draft_mode: bool = False) -> dict[str, str | int | None]:  # type: ignore[no-any-unimported]
    """
    Execute a nano script and record the results.

    Args:
        nano_id: UUID of the nano to execute.
        trigger: How the run was triggered ("schedule" or "manual").
        run_log_id: Optional pre-created RunLog ID (from gateway manual runs).
        draft_mode: If True, sensitive API calls produce draft approvals instead of real ones.
    """
    session = SyncSessionLocal()
    try:
        # Look up the nano
        nano = session.query(Nano).filter(Nano.id == uuid.UUID(nano_id)).first()
        if nano is None:
            raise ValueError(f"Nano not found: {nano_id}")

        # Use pre-created RunLog if provided, otherwise create one
        if run_log_id:
            run_log = session.query(RunLog).filter(RunLog.id == uuid.UUID(run_log_id)).first()
            if run_log is None:
                raise ValueError(f"RunLog not found: {run_log_id}")
        else:
            run_log = RunLog(
                id=uuid.uuid4(),
                nano_id=nano.id,
                trigger=trigger,
                started_at=datetime.utcnow(),
                status="running",
            )
            session.add(run_log)
            session.commit()

        run_log_id = str(run_log.id)

        # Snapshot state before execution
        run_log.state_before = _snapshot_state(session, nano.id)
        session.commit()

        # Execute the nano script
        result = execute_nano(nano, run_log_id, draft_mode=draft_mode)

        # Snapshot state after execution
        state_after = _snapshot_state(session, nano.id)

        # Determine final status based on exit code
        if result["exit_code"] == 0:
            # Check if there are pending approvals linked to this run
            pending_count = (
                session.query(PendingApproval)
                .filter(
                    PendingApproval.run_log_id == uuid.UUID(run_log_id),
                    PendingApproval.status == "pending",
                )
                .count()
            )
            status = "awaiting_approval" if pending_count > 0 else "success"
        else:
            status = "error"

        # Update the RunLog with results
        run_log.stdout = str(result.get("stdout", ""))
        run_log.stderr = str(result.get("stderr", ""))
        run_log.exit_code = int(result["exit_code"])  # type: ignore[arg-type]
        run_log.status = status
        run_log.finished_at = datetime.utcnow()
        run_log.log_file_path = str(result["log_file_path"]) if result.get("log_file_path") else None
        run_log.state_after = state_after
        session.commit()

        return {
            "run_log_id": run_log_id,
            "status": status,
            "exit_code": result["exit_code"],
        }

    except Exception as exc:
        # If something goes wrong, try to mark the run_log as error
        session.rollback()
        try:
            if "run_log" in dir() and run_log is not None:
                run_log.status = "error"
                run_log.stderr = str(exc)
                run_log.finished_at = datetime.utcnow()
                session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()
