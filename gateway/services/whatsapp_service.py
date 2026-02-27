"""WhatsApp service — async subprocess wrapper around the wacli CLI."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WACLI_BIN = "/usr/local/bin/wacli"
WACLI_STORE = "/data/wacli-store"
WACLI_TIMEOUT = 30  # seconds
SYNC_INTERVAL = 60  # seconds between periodic syncs
SYNC_IDLE_EXIT = "15s"  # wacli --idle-exit for periodic sync


# ---------------------------------------------------------------------------
# Sync state tracking (in-process, no persistence needed)
# ---------------------------------------------------------------------------

class SyncStatus(str, Enum):
    IDLE = "idle"
    SYNCING = "syncing"
    READY = "ready"


@dataclass
class SyncState:
    status: SyncStatus = SyncStatus.IDLE
    progress: str = ""
    started_at: datetime | None = None
    pid: int | None = None  # wacli process PID for liveness checks

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "progress": self.progress,
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }


_sync_state = SyncState()

# Serialize all wacli subprocess calls — wacli holds an exclusive lock on
# the store, so concurrent invocations fail with "store is locked".
_wacli_lock = asyncio.Lock()


def get_sync_state() -> dict[str, Any]:
    """Return the current WhatsApp sync state.

    If status is SYNCING but the wacli process is no longer alive,
    auto-transition to READY (prevents stuck state).
    """
    if _sync_state.status == SyncStatus.SYNCING and _sync_state.pid is not None:
        try:
            os.kill(_sync_state.pid, 0)  # check if process exists
        except ProcessLookupError:
            # wacli exited but state wasn't updated — fix it now
            logger.info("wacli PID %d no longer alive — marking sync as ready", _sync_state.pid)
            _sync_state.status = SyncStatus.READY
            _sync_state.progress = "Sync complete"
            _sync_state.pid = None
        except OSError:
            pass  # permission error etc — process exists
    return _sync_state.to_dict()


def _cleanup_stale_lock() -> None:
    """Remove the wacli LOCK file if the holding process is dead."""
    lock_path = os.path.join(WACLI_STORE, "LOCK")
    if not os.path.exists(lock_path):
        return
    try:
        with open(lock_path) as f:
            lock_content = f.read()
        for part in lock_content.split("\n"):
            if part.startswith("pid="):
                pid = int(part.split("=", 1)[1])
                try:
                    os.kill(pid, 0)
                    return  # process is alive — lock is valid
                except ProcessLookupError:
                    pass  # dead — safe to remove
        os.remove(lock_path)
        logger.info("Cleaned up stale wacli lock file")
    except (OSError, ValueError):
        pass


async def _run_wacli(*args: str, timeout: float = WACLI_TIMEOUT) -> dict[str, Any]:
    """Run a wacli command and return parsed JSON output.

    Acquires _wacli_lock so only one wacli process runs at a time (wacli
    holds an exclusive file lock on the store).  Waits up to the full
    timeout for the lock (sync may be running), then runs the command.
    """
    if _sync_state.status == SyncStatus.SYNCING:
        raise RuntimeError("WhatsApp is syncing conversations, please wait until sync completes")
    async with _wacli_lock:
        _cleanup_stale_lock()
        cmd = [WACLI_BIN, "--store", WACLI_STORE, "--json"] + list(args)
        logger.debug("Running wacli: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"wacli error (exit {proc.returncode}): {stderr.decode().strip()}")
        result: dict[str, Any] = json.loads(stdout.decode())
        return result


async def list_chats(limit: int, session: AsyncSession) -> Any:
    """List recent WhatsApp chats."""
    return await _run_wacli("chats", "list", "--limit", str(limit))


async def list_messages(
    chat_jid: str | None, limit: int,
    before: str | None, after: str | None,
    session: AsyncSession,
) -> Any:
    """List WhatsApp messages, optionally filtered by chat and time range."""
    args = ["messages", "list", "--limit", str(limit)]
    if chat_jid:
        args.extend(["--chat", chat_jid])
    if before:
        args.extend(["--before", before])
    if after:
        args.extend(["--after", after])
    return await _run_wacli(*args)


def _sanitize_fts5_query(query: str) -> str:
    """Escape special FTS5 characters by wrapping the query in double quotes.

    SQLite FTS5 treats characters like @, *, +, -, ^, ~ as operators.
    Wrapping in double quotes makes the entire query a literal phrase match.
    Internal double quotes are escaped by doubling them.
    """
    return '"' + query.replace('"', '""') + '"'


async def search_messages(query: str, session: AsyncSession) -> Any:
    """Search WhatsApp messages (offline search of synced messages)."""
    safe_query = _sanitize_fts5_query(query)
    return await _run_wacli("messages", "search", safe_query)


async def list_groups(session: AsyncSession) -> Any:
    """List WhatsApp group chats."""
    return await _run_wacli("groups", "list")


async def send_text(to: str, message: str, session: AsyncSession) -> Any:
    """Send a WhatsApp text message."""
    return await _run_wacli("send", "text", "--to", to, "--message", message)


async def send_file(to: str, file_path: str, caption: str | None, session: AsyncSession) -> Any:
    """Send a file via WhatsApp."""
    args = ["send", "file", "--to", to, "--file", file_path]
    if caption:
        args.extend(["--caption", caption])
    return await _run_wacli(*args)


async def download_media(chat_jid: str, message_id: str, session: AsyncSession) -> Any:
    """Download a media attachment from a WhatsApp message."""
    return await _run_wacli("media", "download", "--chat", chat_jid, "--id", message_id)


async def history_backfill(chat_jid: str, requests: int, count: int, session: AsyncSession) -> Any:
    """Fetch older WhatsApp messages from the primary device."""
    return await _run_wacli(
        "history", "backfill",
        "--chat", chat_jid,
        "--requests", str(requests),
        "--count", str(count),
    )


async def test_all(session: AsyncSession) -> list[dict[str, Any]]:
    """Health check — try listing 1 chat."""
    try:
        await _run_wacli("chats", "list", "--limit", "1")
        return [{"name": "whatsapp.chats.list", "success": True, "detail": "OK"}]
    except Exception as e:
        return [{"name": "whatsapp.chats.list", "success": False, "detail": str(e)}]


# ---------------------------------------------------------------------------
# Periodic background sync
# ---------------------------------------------------------------------------

async def _run_sync_once() -> None:
    """Run ``wacli sync --once`` to pull new messages from WhatsApp.

    Acquires _wacli_lock so it doesn't conflict with other wacli commands.
    """
    async with _wacli_lock:
        _cleanup_stale_lock()
        cmd = [
            WACLI_BIN, "sync", "--once",
            "--idle-exit", SYNC_IDLE_EXIT,
            "--store", WACLI_STORE,
        ]
        logger.debug("Periodic sync: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120,
            )
            if proc.returncode == 0:
                logger.info("Periodic sync completed successfully")
            else:
                logger.warning(
                    "Periodic sync exited %d: %s",
                    proc.returncode, stderr.decode().strip(),
                )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("Periodic sync timed out after 120s")


async def run_periodic_sync() -> None:
    """Background loop that syncs WhatsApp messages every SYNC_INTERVAL seconds.

    Only runs if WhatsApp is authenticated. Skips iterations when
    the initial auth sync is in progress.
    """
    # Wait a bit before first sync to let the gateway start up
    await asyncio.sleep(30)

    while True:
        try:
            # Skip if currently in auth/sync flow
            if _sync_state.status == SyncStatus.SYNCING:
                logger.debug("Periodic sync skipped: auth sync in progress")
            else:
                # Check if authenticated
                status = await auth_status()
                if status.get("authenticated"):
                    await _run_sync_once()
                else:
                    logger.debug("Periodic sync skipped: not authenticated")
        except Exception:
            logger.exception("Periodic sync error")

        await asyncio.sleep(SYNC_INTERVAL)


# ---------------------------------------------------------------------------
# Auth helpers (QR code flow, status, logout)
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def auth_stream() -> AsyncGenerator[str, None]:
    """Run ``wacli auth`` and yield SSE events for QR codes, connection, and sync progress.

    wacli prints QR codes as Unicode block art (█▄▀ characters) to stdout.
    We detect QR blocks by looking for lines containing runs of █ characters,
    accumulate them, and emit each complete QR as an SSE ``qr`` event.

    After authentication succeeds ("Connected"), wacli continues running to
    sync conversations. We track this sync phase and stream progress to the
    client, only sending ``done`` when wacli exits naturally.
    """
    global _sync_state

    # Clean up stale lock, or kill the holder so we can take over
    lock_path = os.path.join(WACLI_STORE, "LOCK")
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                lock_content = f.read()
            for part in lock_content.split("\n"):
                if part.startswith("pid="):
                    pid = int(part.split("=", 1)[1])
                    try:
                        os.kill(pid, 0)
                        # Process is alive — kill it so we can take over
                        os.kill(pid, signal.SIGTERM)
                        await asyncio.sleep(1)
                    except ProcessLookupError:
                        pass  # already dead
            os.remove(lock_path)
            logger.info("Cleaned up wacli lock file")
        except (OSError, ValueError):
            pass

    cmd = [WACLI_BIN, "auth", "--store", WACLI_STORE, "--idle-exit", "60s"]
    logger.info("Starting wacli auth: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout (QR goes to stderr)
    )

    qr_lines: list[str] = []
    in_qr = False
    connected = False
    done_sent = False
    handed_off = False  # True when background task owns the process

    try:
        assert proc.stdout is not None
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="replace").rstrip("\n")

            # Detect QR block lines: contain ██ runs, or ▀▀ runs (bottom border),
            # or ▄▄ runs (top border variant)
            is_qr_line = "██" in line or "▀▀" in line or "▄▄" in line

            if is_qr_line:
                if not in_qr:
                    in_qr = True
                    qr_lines = []
                qr_lines.append(line)
            else:
                if in_qr and qr_lines:
                    # End of a QR block — emit it
                    qr_text = "\n".join(qr_lines)
                    yield _sse("qr", {"qr": qr_text})
                    qr_lines = []
                    in_qr = False

                # Non-QR line: emit as log so the user sees full output
                stripped = line.strip()
                if stripped:
                    # Strip ANSI escape codes for clean display
                    clean = re.sub(r'\x1b\[[0-9;]*m', '', stripped)
                    # Skip the "Scan this QR code" prompt — we show our own
                    if clean and not clean.startswith("Scan this QR"):
                        if not connected:
                            yield _sse("status", {"message": clean})
                        else:
                            # In sync phase — send as sync progress
                            _sync_state.progress = clean
                            yield _sse("sync", {"message": clean})

                    # Detect successful connection — enter sync phase
                    if not connected and "Connected" in stripped:
                        connected = True
                        _sync_state.status = SyncStatus.SYNCING
                        _sync_state.started_at = datetime.now()
                        _sync_state.pid = proc.pid
                        _sync_state.progress = "Starting sync..."
                        yield _sse("connected", {"message": "Connected! Syncing conversations..."})

        # Flush any trailing QR block
        if qr_lines:
            qr_text = "\n".join(qr_lines)
            yield _sse("qr", {"qr": qr_text})

    except asyncio.CancelledError:
        # Client disconnected (closed modal / navigated away)
        if connected:
            # Hand off to background task — it owns the process now.
            # Do NOT await anything here; the framework is cancelling us.
            logger.info("SSE client disconnected during sync — wacli continues in background")
            handed_off = True
            asyncio.create_task(_wait_for_sync_completion(proc))
            # PID is already stored in _sync_state for liveness checks
            return
        proc.kill()
        raise
    finally:
        # Only wait for the process if we still own it
        if not handed_off and proc.returncode is None:
            await proc.wait()

    # wacli exited naturally — sync is complete (or auth failed)
    if connected:
        _sync_state.status = SyncStatus.READY
        _sync_state.progress = "Sync complete"
        _sync_state.pid = None
        done_sent = True
        yield _sse("done", {"success": True})

    if not done_sent:
        success = False
        try:
            status = await auth_status()
            success = status.get("authenticated", False)
        except Exception:
            pass
        yield _sse("done", {"success": success})


async def _wait_for_sync_completion(proc: asyncio.subprocess.Process) -> None:
    """Background task: wait for wacli to exit and update sync state.

    Called when the SSE client disconnects during sync. Reads remaining
    output to keep the pipe from blocking, then marks sync as ready.

    Even if this task fails, ``get_sync_state()`` will detect the dead PID
    and auto-transition to READY as a fallback.
    """
    global _sync_state
    try:
        if proc.stdout is not None:
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").strip()
                if line:
                    clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
                    if clean:
                        _sync_state.progress = clean
        await proc.wait()
        _sync_state.status = SyncStatus.READY
        _sync_state.progress = "Sync complete"
        _sync_state.pid = None
        logger.info("wacli sync completed in background")
    except Exception:
        logger.exception("Error waiting for wacli sync completion")
        _sync_state.status = SyncStatus.READY
        _sync_state.progress = "Sync finished (with errors)"
        _sync_state.pid = None


async def auth_status() -> dict[str, Any]:
    """Check WhatsApp authentication status.

    Returns a flat dict with ``{"authenticated": bool, ...}``.
    wacli wraps its JSON in ``{"success":…,"data":{…},"error":…}``
    so we unwrap the ``data`` field.
    """
    cmd = [WACLI_BIN, "auth", "status", "--store", WACLI_STORE, "--json"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            parsed: dict[str, Any] = json.loads(stdout.decode())
            # Unwrap wacli envelope: {"success":…,"data":{…},"error":…}
            if isinstance(parsed.get("data"), dict):
                data: dict[str, Any] = parsed["data"]
                return data
            return parsed
    except Exception:
        pass

    # Fallback: try wacli doctor
    cmd2 = [WACLI_BIN, "doctor", "--store", WACLI_STORE, "--json"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd2,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            parsed2: dict[str, Any] = json.loads(stdout.decode())
            if isinstance(parsed2.get("data"), dict):
                data2: dict[str, Any] = parsed2["data"]
                return data2
            return parsed2
    except Exception:
        pass

    return {"authenticated": False, "error": "Unable to determine status"}


async def auth_logout() -> dict[str, Any]:
    """Invalidate the WhatsApp session."""
    cmd = [WACLI_BIN, "auth", "logout", "--store", WACLI_STORE, "--json"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(f"wacli logout error (exit {proc.returncode}): {stderr.decode().strip()}")
    result: dict[str, Any] = json.loads(stdout.decode())
    return result
