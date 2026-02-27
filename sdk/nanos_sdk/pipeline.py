"""Pipeline visualization for nano runs.

Tracks stage status/progress and flushes state to .pipeline.json
so the dashboard can render a live workflow diagram.
"""

from __future__ import annotations

import atexit
import json
import os
import tempfile
import threading
import time


class Stage:
    __slots__ = ("id", "label", "status", "progress", "output", "detail")

    id: str
    label: str
    status: str
    progress: float | None
    output: str | None
    detail: str | None

    def __init__(self, id: str, label: str):
        self.id = id
        self.label = label
        self.status = "pending"
        self.progress = None  # float 0.0-1.0 or None
        self.output = None    # str or None
        self.detail = None    # str or None — long-form log/output for modal

    def to_dict(self) -> dict[str, str | float]:
        d: dict[str, str | float] = {"id": self.id, "label": self.label, "status": self.status}
        if self.progress is not None:
            d["progress"] = round(self.progress, 3)
        if self.output is not None:
            d["output"] = self.output
        if self.detail is not None:
            d["detail"] = self.detail
        return d


class Pipeline:
    """Track pipeline stages and flush state to disk for dashboard visualization.

    Usage::

        pipe = Pipeline([
            ("fetch", "Fetch Emails"),
            ("analyze", "Analyze Threads"),
        ])
        pipe.start("fetch")
        pipe.progress("fetch", 50, 200)
        pipe.done("fetch", output="200 messages")
        pipe.close()
    """

    def __init__(self, stages: list[tuple[str, str]]):
        self._lock = threading.Lock()
        self._stages: dict[str, Stage] = {}
        self._order: list[str] = []
        for item in stages:
            sid, label = item[0], item[1]
            self._stages[sid] = Stage(sid, label)
            self._order.append(sid)

        self._dirty = True
        self._closed = False

        # Determine output path
        self._path: str | None = None
        log_dir = os.environ.get("NANO_LOG_DIR", "")
        if log_dir:
            self._path = os.path.join(log_dir, ".pipeline.json")

        # Background flush thread
        if self._path:
            self._thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._thread.start()
            atexit.register(self.close)
            # Initial flush
            self._flush()

    def start(self, stage_id: str) -> None:
        with self._lock:
            s = self._stages.get(stage_id)
            if s:
                s.status = "running"
                s.progress = None
                self._dirty = True

    def progress(self, stage_id: str, current: int, total: int) -> None:
        with self._lock:
            s = self._stages.get(stage_id)
            if s:
                s.status = "running"
                s.progress = current / total if total > 0 else 0.0
                s.output = f"{current}/{total}"
                self._dirty = True

    def waiting(self, stage_id: str, output: str | None = None, detail: str | None = None) -> None:
        with self._lock:
            s = self._stages.get(stage_id)
            if s:
                s.status = "waiting"
                if output is not None:
                    s.output = output
                if detail is not None:
                    s.detail = detail
                self._dirty = True

    def done(self, stage_id: str, output: str | None = None, detail: str | None = None) -> None:
        with self._lock:
            s = self._stages.get(stage_id)
            if s:
                s.status = "done"
                s.progress = None
                if output is not None:
                    s.output = output
                if detail is not None:
                    s.detail = detail
                self._dirty = True

    def error(self, stage_id: str, output: str | None = None, detail: str | None = None) -> None:
        with self._lock:
            s = self._stages.get(stage_id)
            if s:
                s.status = "error"
                if output is not None:
                    s.output = output
                if detail is not None:
                    s.detail = detail
                self._dirty = True

    def log(self, stage_id: str, line: str) -> None:
        """Append a line to a stage's detail text."""
        with self._lock:
            s = self._stages.get(stage_id)
            if s:
                if s.detail is None:
                    s.detail = line
                else:
                    s.detail += "\n" + line
                self._dirty = True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._flush()

    def _flush_loop(self):
        while True:
            with self._lock:
                if self._closed:
                    return
            time.sleep(0.2)
            self._flush()

    def _flush(self):
        if not self._path:
            return
        with self._lock:
            if not self._dirty:
                return
            data = {"stages": [self._stages[sid].to_dict() for sid in self._order]}
            self._dirty = False

        # Atomic write
        dir_path = os.path.dirname(self._path)
        try:
            fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._path)
        except OSError:
            pass
