"""Hand-written base client for the Nanos SDK.

Provides a synchronous wrapper around the gateway API for use in nano scripts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, TypedDict

import httpx

from nanos_sdk import models


class ApiCallEntry(TypedDict):
    """Shape of each line in .api_calls.jsonl."""
    call_id: str
    ts: float
    method: str
    path: str
    label: str
    status_code: int | None
    duration_ms: float
    error: str | None
    approval_id: str | None
    request_params: str | None
    request_body: str | None
    response_body: str | None


logger = logging.getLogger("nanos_sdk")


# ------------------------------------------------------------------ #
# API-call tracing — writes a JSONL file alongside the run logs
# ------------------------------------------------------------------ #

def _get_api_calls_path() -> str | None:
    log_dir = os.environ.get("NANO_LOG_DIR")
    if not log_dir:
        return None
    return f"{log_dir}.api_calls.jsonl"


_API_CALLS_PATH: str | None = _get_api_calls_path()


def _truncate(obj: Any, max_len: int = 8000) -> str | None:
    if obj is None:
        return None
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _write_api_call(
    call_id: str,
    method: str,
    path: str,
    params: dict[str, Any] | None,
    request_body: Any,
    status_code: int | None,
    duration_ms: float,
    response_body: Any,
    error: str | None,
) -> None:
    if not _API_CALLS_PATH:
        return
    try:
        label = path
        if label.startswith("/api/"):
            label = label[5:]
        approval_id = None
        if isinstance(response_body, dict):
            approval_id = response_body.get("approval_id")
        entry: ApiCallEntry = {
            "call_id": call_id,
            "ts": time.time(),
            "method": method,
            "path": path,
            "label": label,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 1),
            "error": error,
            "approval_id": approval_id,
            "request_params": _truncate(params),
            "request_body": _truncate(request_body),
            "response_body": _truncate(response_body),
        }
        with open(_API_CALLS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


class NanosAPIError(Exception):
    """Raised when the gateway returns a non-2xx response (except 202)."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class StateTypeError(Exception):
    """Raised when state value type doesn't match requested type."""


class _JSONLineHandler(logging.Handler):
    """Logging handler that writes one JSON object per line."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            if record.exc_info and record.exc_info[0] is not None:
                entry["exception"] = self.formatter.formatException(record.exc_info) if self.formatter else str(record.exc_info[1])
            self._file.write(json.dumps(entry) + "\n")
            self._file.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def close(self) -> None:
        self._file.close()
        super().close()


class _NanoOnlyFilter(logging.Filter):
    """Pass only records from nano scripts and nanos_sdk, not third-party libs."""

    _ALLOWED_PREFIXES = ("nanos_sdk",)

    def filter(self, record: logging.LogRecord) -> bool:
        # Allow nanos_sdk and any logger that is NOT a known third-party lib
        name = record.name
        if name.startswith(("httpx", "httpcore", "hpack", "urllib3",
                            "google", "openai", "celery", "kombu",
                            "amqp", "asyncio", "sqlalchemy", "pydantic")):
            return False
        return True


def _configure_logging() -> None:
    """Configure logging with a StreamHandler and file handlers.

    Creates two log files when NANO_LOG_DIR is set:
      - ``{id}.log``       — INFO+, nano/SDK logs only (shown in dashboard)
      - ``{id}.debug.log`` — DEBUG, everything including third-party libs
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Always add a stream handler for console output (nano-only).
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(fmt)
    stream_handler.addFilter(_NanoOnlyFilter())
    root.addHandler(stream_handler)

    # If NANO_LOG_DIR is set, add file-based handlers.
    log_dir = os.environ.get("NANO_LOG_DIR")
    if log_dir:
        # Standard log — nano/SDK only, INFO+  (shown in dashboard)
        text_path = f"{log_dir}.log"
        file_handler = logging.FileHandler(text_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(fmt)
        file_handler.addFilter(_NanoOnlyFilter())
        root.addHandler(file_handler)

        # Debug log — everything, DEBUG+
        debug_path = f"{log_dir}.debug.log"
        debug_handler = logging.FileHandler(debug_path, encoding="utf-8")
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(fmt)
        root.addHandler(debug_handler)

        # JSON-lines log file — everything, DEBUG+
        json_path = f"{log_dir}.json"
        json_handler = _JSONLineHandler(json_path)
        json_handler.setLevel(logging.DEBUG)
        json_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(json_handler)


# Configure logging once at module import time.
_configure_logging()


class NanosClient:
    """Synchronous client for the Nanos gateway API.

    Reads configuration from environment variables:
        NANO_API_KEY      — API key sent as ``X-Nano-Key`` header.
        NANO_GATEWAY_URL  — Base URL of the gateway (default ``http://localhost:8000``).
        NANO_LOG_DIR      — Optional directory prefix for log files.
    """

    def __init__(
        self,
        api_key: str | None = None,
        gateway_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("NANO_API_KEY", "")
        self.gateway_url = (
            gateway_url or os.environ.get("NANO_GATEWAY_URL", "http://localhost:8000")
        ).rstrip("/")

        headers = {"X-Nano-Key": self.api_key}
        run_log_id = os.environ.get("NANO_RUN_LOG_ID")
        if run_log_id:
            headers["X-Nano-Run-Log-Id"] = run_log_id
        if os.environ.get("NANO_DRAFT_MODE") == "true":
            headers["X-Draft-Mode"] = "true"

        self._client = httpx.Client(
            base_url=self.gateway_url,
            headers=headers,
            timeout=httpx.Timeout(connect=10, read=300, write=30, pool=30),
        )
        logger.debug(
            "NanosClient initialised — gateway=%s", self.gateway_url
        )

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a request and return the parsed JSON response.

        Raises ``NanosAPIError`` for non-2xx status codes (202 is accepted).
        """
        call_id = os.urandom(3).hex()[:5]
        label = path[5:] if path.startswith("/api/") else path
        logger.debug("%s %s params=%s body=%s", method, path, params, json_body)
        t0 = time.monotonic()
        try:
            response = self._client.request(
                method, path, json=json_body, params=params, headers=headers or {},
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.info("[%s] %s %s → ERR (%dms)", call_id, method, label, round(duration_ms))
            _write_api_call(call_id, method, path, params, json_body, None, duration_ms, None, str(exc))
            raise

        duration_ms = (time.monotonic() - t0) * 1000

        if not (200 <= response.status_code < 300):
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except Exception:  # noqa: BLE001
                pass
            logger.info("[%s] %s %s → %d (%dms)", call_id, method, label, response.status_code, round(duration_ms))
            _write_api_call(call_id, method, path, params, json_body, response.status_code, duration_ms, detail, None)
            raise NanosAPIError(response.status_code, detail)

        if response.status_code == 204 or not response.content:
            logger.info("[%s] %s %s → %d (%dms)", call_id, method, label, response.status_code, round(duration_ms))
            _write_api_call(call_id, method, path, params, json_body, response.status_code, duration_ms, None, None)
            return {}
        result = response.json()
        logger.info("[%s] %s %s → %d (%dms)", call_id, method, label, response.status_code, round(duration_ms))
        _write_api_call(call_id, method, path, params, json_body, response.status_code, duration_ms, result, None)
        return result

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        cleaned = {k: v for k, v in params.items() if v is not None}
        result = self._request("GET", path, params=cleaned)
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from GET {path}, got {type(result).__name__}")
        return result

    def _get_list(self, path: str, **params: Any) -> list[Any]:
        cleaned = {k: v for k, v in params.items() if v is not None}
        result = self._request("GET", path, params=cleaned)
        if not isinstance(result, list):
            raise TypeError(f"Expected list from GET {path}, got {type(result).__name__}")
        return result

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._request("POST", path, json_body=body)
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from POST {path}, got {type(result).__name__}")
        return result

    def _put(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._request("PUT", path, json_body=body)
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from PUT {path}, got {type(result).__name__}")
        return result

    def _patch(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._request("PATCH", path, json_body=body)
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from PATCH {path}, got {type(result).__name__}")
        return result

    def _delete(self, path: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        result = self._request("DELETE", path, headers=headers)
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from DELETE {path}, got {type(result).__name__}")
        return result

    # --------------------------------------------------------------------- #
    # Parameters
    # --------------------------------------------------------------------- #

    @property
    def parameters(self) -> dict[str, Any]:
        """Return nano instance parameters from NANO_PARAMETERS env var."""
        raw = os.environ.get("NANO_PARAMETERS", "")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return {}
            return parsed
        except (json.JSONDecodeError, TypeError):
            return {}

    def get_parameter(self, key: str, default: Any = None) -> Any:
        """Get a single parameter value by key."""
        return self.parameters.get(key, default)

    # --------------------------------------------------------------------- #
    # OpenAI
    # --------------------------------------------------------------------- #

    def openai_chat(
        self,
        messages: list[dict[str, Any]],
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> models.ChatResponse:
        """Send a chat completion request via the gateway.

        Returns a ChatResponse with ``.content``, ``.model``,
        ``.usage``, ``.tool_calls``, and ``.finish_reason`` attributes.
        """
        body: dict[str, Any] = {
            "messages": messages,
            "model": model,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if response_format is not None:
            body["response_format"] = response_format
        if tools is not None:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        logger.info("openai_chat model=%s messages=%d", model, len(messages))
        result = self._post("/api/openai/chat", body)
        return models.ChatResponse(**result)

    def openai_embeddings(
        self,
        input: str | list[str],  # noqa: A002
        model: str = "text-embedding-3-small",
    ) -> models.EmbeddingResponse:
        """Generate embeddings via the gateway.

        Returns an EmbeddingResponse with ``.embeddings``, ``.model``, and ``.usage``.
        """
        body: dict[str, Any] = {"input": input, "model": model}
        logger.info("openai_embeddings model=%s", model)
        return models.EmbeddingResponse(**self._post("/api/openai/embeddings", body))


    # --------------------------------------------------------------------- #
    # State store
    # --------------------------------------------------------------------- #

    def _state_get(self, key: str, expected_type: str, default: Any = None) -> Any:
        """Internal helper: get a state value and verify its type."""
        resp = self._get(f"/api/state/{key}")
        if not resp.get("found"):
            return default
        if resp["value_type"] != expected_type:
            raise StateTypeError(
                f"Key '{key}': expected {expected_type}, got {resp['value_type']}"
            )
        return resp["value"]

    def _state_set(self, key: str, value: Any, value_type: str) -> None:
        """Internal helper: set a state value with type tag."""
        self._put(f"/api/state/{key}", {"value": value, "value_type": value_type})

    def state_get_string(self, key: str, default: str | None = None) -> str | None:
        result = self._state_get(key, "string", default)
        assert result is None or isinstance(result, str)
        return result

    def state_set_string(self, key: str, value: str) -> None:
        self._state_set(key, value, "string")

    def state_get_int(self, key: str, default: int | None = None) -> int | None:
        result = self._state_get(key, "int", default)
        assert result is None or isinstance(result, int)
        return result

    def state_set_int(self, key: str, value: int) -> None:
        self._state_set(key, value, "int")

    def state_get_float(self, key: str, default: float | None = None) -> float | None:
        result = self._state_get(key, "float", default)
        assert result is None or isinstance(result, (int, float))
        return result

    def state_set_float(self, key: str, value: float) -> None:
        self._state_set(key, value, "float")

    def state_get_bool(self, key: str, default: bool | None = None) -> bool | None:
        result = self._state_get(key, "bool", default)
        assert result is None or isinstance(result, bool)
        return result

    def state_set_bool(self, key: str, value: bool) -> None:
        self._state_set(key, value, "bool")

    def state_get_json(self, key: str, default: Any = None) -> Any:
        return self._state_get(key, "json", default)

    def state_set_json(self, key: str, value: Any) -> None:
        self._state_set(key, value, "json")

    def state_delete(self, key: str) -> bool:
        """Delete a state key. Returns True if the key existed."""
        resp = self._delete(f"/api/state/{key}")
        return bool(resp.get("deleted", False))

    # --------------------------------------------------------------------- #
    # Approvals
    # --------------------------------------------------------------------- #

    def wait_for_approval(
        self,
        approval_id: str,
        timeout: int = 300,
        poll_interval: int = 5,
    ) -> models.ApprovalStatusOut:
        """Poll an approval until it is resolved or *timeout* seconds elapse.

        Returns an ApprovalStatusOut with ``.id``, ``.status``, and ``.response_body``.
        Raises ``TimeoutError`` if the approval is not resolved in time.
        """
        logger.info(
            "wait_for_approval id=%s timeout=%ds poll=%ds",
            approval_id,
            timeout,
            poll_interval,
        )
        deadline = time.monotonic() + timeout
        while True:
            result = models.ApprovalStatusOut(
                **self._get(f"/api/approvals/{approval_id}/status")
            )
            status = result.status
            if status not in ("pending",):
                logger.info("Approval %s resolved: %s", approval_id, status)
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Approval {approval_id} not resolved within {timeout}s"
                )
            time.sleep(min(poll_interval, remaining))
