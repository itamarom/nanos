"""
Sandboxed Docker execution engine for nanos.

Runs each nano script in an ephemeral Docker container on the ``nano-exec``
network, which can only reach the API gateway.  The container has no access
to PostgreSQL, Redis, or the ``shared`` package — eliminating credential
theft, queue poisoning, and direct DB access.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import TYPE_CHECKING

import docker
from docker.errors import ContainerError, ImageNotFound, APIError

if TYPE_CHECKING:
    from shared.models import Nano

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.database import SyncSessionLocal
from shared.models import NanoApiKey

logger = logging.getLogger(__name__)

# Configuration from environment (set in docker-compose.yml)
NANO_RUNNER_IMAGE = os.environ.get("NANO_RUNNER_IMAGE", "nanos-nano-runner")
NANO_EXEC_NETWORK = os.environ.get("NANO_EXEC_NETWORK", "nano-exec")
_PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "")
NANO_HOST_NANOS_PATH = os.environ.get("NANO_HOST_NANOS_PATH", "")
NANO_HOST_LOGS_PATH = os.environ.get("NANO_HOST_LOGS_PATH", "")
NANO_TIMEOUT = int(os.environ.get("NANO_TIMEOUT", "300"))


def _resolve_host_path(path: str) -> str:
    """Resolve a relative path against PROJECT_ROOT (the host's project dir)."""
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    if _PROJECT_ROOT:
        return os.path.normpath(os.path.join(_PROJECT_ROOT, path))
    return path
NANO_MEM_LIMIT = os.environ.get("NANO_MEM_LIMIT", "512m")

# Singleton Docker client (connects via mounted /var/run/docker.sock)
_docker_client: docker.DockerClient | None = None


def _get_docker_client() -> docker.DockerClient:
    """Return a cached Docker client, creating it on first call."""
    global _docker_client  # noqa: PLW0603
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


def execute_nano(nano: Nano, run_log_id: str, draft_mode: bool = False) -> dict[str, str | int | None]:
    """
    Execute a nano's script in an ephemeral Docker container.

    Spins up a throwaway container from the ``nano-runner`` image on the
    ``nano-exec`` network.  The container can only reach the API gateway —
    no database, Redis, or other infrastructure is accessible.

    Args:
        nano: A Nano ORM instance (must have .id, .name, .script_path).
        run_log_id: The UUID string of the associated RunLog entry.
        draft_mode: If True, set NANO_DRAFT_MODE=true in container env.

    Returns:
        dict with keys: stdout, stderr, exit_code, log_file_path.
    """
    nano_name = nano.name
    log_dir = os.path.join("/var/log/nanos", nano_name, run_log_id)
    os.makedirs(log_dir, exist_ok=True)

    # Look up the nano's active API key
    api_key = _get_api_key(nano.id)

    # Resolve script path: instance override or type default
    script_path = nano.script_path
    if not script_path and hasattr(nano, "type_name") and nano.type_name:
        script_path = f"{nano.type_name}/nano.py"

    # Prevent path traversal — script must stay under /nanos/
    from shared.nano_types import safe_resolve
    try:
        safe_resolve("/nanos", script_path)
    except ValueError:
        return {
            "stdout": "",
            "stderr": f"Security error: script_path escapes /nanos/: {script_path}",
            "exit_code": -1,
            "log_file_path": log_dir,
        }

    # Build container environment — only what nanos need
    env: dict[str, str] = {
        "NANO_API_KEY": api_key or "",
        "NANO_GATEWAY_URL": "http://api-gateway:8000",
        "NANO_LOG_DIR": f"/var/log/nanos/{nano_name}/{run_log_id}",
        "NANO_RUN_LOG_ID": run_log_id,
    }

    if draft_mode:
        env["NANO_DRAFT_MODE"] = "true"

    if nano.parameters:
        env["NANO_PARAMETERS"] = nano.parameters

    perms = _get_permissions(nano.id)
    if perms:
        env["NANO_PERMISSIONS"] = ",".join(perms)

    # Volume mounts — must use HOST paths since we talk to the host Docker daemon
    volumes: dict[str, dict[str, str]] = {}
    nanos_host = _resolve_host_path(NANO_HOST_NANOS_PATH)
    logs_host = _resolve_host_path(NANO_HOST_LOGS_PATH)
    if nanos_host:
        volumes[nanos_host] = {"bind": "/nanos", "mode": "ro"}
    if logs_host:
        volumes[logs_host] = {"bind": "/var/log/nanos", "mode": "rw"}

    client = _get_docker_client()
    container = None

    try:
        container = client.containers.run(
            image=NANO_RUNNER_IMAGE,
            command=["python", f"/nanos/{script_path}"],
            environment=env,
            volumes=volumes,
            network=NANO_EXEC_NETWORK,
            mem_limit=NANO_MEM_LIMIT,
            detach=True,
        )

        # Wait for completion with timeout
        result = container.wait(timeout=NANO_TIMEOUT)
        exit_code: int = result.get("StatusCode", -1)

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "log_file_path": log_dir,
        }

    except Exception as exc:
        # Handle timeouts (ConnectionError from docker-py on wait timeout),
        # image-not-found, API errors, and anything else.
        error_msg = str(exc)

        if isinstance(exc, ImageNotFound):
            error_msg = f"Nano runner image not found: {NANO_RUNNER_IMAGE}"
        elif isinstance(exc, (ContainerError, APIError)):
            error_msg = f"Docker error: {exc}"
        elif "timed out" in error_msg.lower() or "read timed out" in error_msg.lower():
            error_msg = f"Nano execution timed out after {NANO_TIMEOUT}s"
            # Kill the timed-out container
            if container is not None:
                try:
                    container.stop(timeout=5)
                except Exception:
                    pass

        logger.error("Nano execution failed for %s: %s", nano_name, error_msg)

        # Try to capture any partial output
        stdout = ""
        stderr = ""
        if container is not None:
            try:
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            except Exception:
                pass

        return {
            "stdout": stdout,
            "stderr": stderr or error_msg,
            "exit_code": -1,
            "log_file_path": log_dir,
        }

    finally:
        # Always clean up the container
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def _get_api_key(nano_id: uuid.UUID) -> str | None:
    """Retrieve the first active API key for a given nano from the database."""
    session = SyncSessionLocal()
    try:
        key_row = (
            session.query(NanoApiKey)
            .filter(NanoApiKey.nano_id == nano_id, NanoApiKey.is_active.is_(True))
            .first()
        )
        return key_row.key if key_row else None
    finally:
        session.close()


def _get_permissions(nano_id: uuid.UUID) -> list[str]:
    """Retrieve all permission endpoints for a nano (instance + type)."""
    from shared.models import Nano, NanoPermission
    session = SyncSessionLocal()
    try:
        # Instance permissions from DB
        rows = session.query(NanoPermission).filter(NanoPermission.nano_id == nano_id).all()
        perms = {r.endpoint for r in rows}

        # Type permissions from config.yaml
        nano = session.query(Nano).filter(Nano.id == nano_id).first()
        if nano and nano.type_name:
            from shared.nano_types import load_type
            type_config = load_type(nano.type_name)
            if type_config:
                perms |= set(type_config.get("permissions", []))

        return sorted(perms)
    finally:
        session.close()
