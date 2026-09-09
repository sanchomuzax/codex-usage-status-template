"""Read Codex rate limits through the local app-server JSON-RPC protocol."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import Any


class AppServerError(RuntimeError):
    """The local Codex app server could not return a quota snapshot."""


def fetch_rate_limits(
    command: Sequence[str] = ("codex", "app-server"), *, timeout_seconds: float = 20.0
) -> dict[str, Any]:
    """Return the raw result of ``account/rateLimits/read``.

    The caller owns persistence and must not store opaque reset-credit IDs
    unless it genuinely needs to redeem a credit.
    """
    return call_app_server("account/rateLimits/read", command, timeout_seconds=timeout_seconds)


def fetch_usage(
    command: Sequence[str] = ("codex", "app-server"), *, timeout_seconds: float = 20.0
) -> dict[str, Any]:
    """Return the raw result of ``account/usage/read``.

    This is the server's own token accounting -- the same figures the Codex
    ``/usage`` view shows -- so it needs no local log scanning and no guesswork.
    """
    return call_app_server("account/usage/read", command, timeout_seconds=timeout_seconds)


def call_app_server(
    method: str,
    command: Sequence[str] = ("codex", "app-server"),
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Return the result of one read-only app-server method.

    This starts a short-lived local Codex app-server process.  It sends no
    ``turn/start`` request, so the operation only reads account state and does
    not invoke the model.
    """

    requests = (
        {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "codex-usage-status",
                    "title": "Codex Usage Status",
                    "version": "0.3.2",
                }
            },
        },
        {"method": "initialized", "params": {}},
        {"method": method, "id": 2},
    )
    payload = "".join(json.dumps(request, separators=(",", ":")) + "\n" for request in requests)

    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AppServerError(f"Codex executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppServerError("Codex app-server timed out while reading rate limits") from exc

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_read_lines, args=(process.stdout, lines), daemon=True)
    reader.start()

    try:
        process.stdin.write(payload)
        process.stdin.flush()
        deadline = time.monotonic() + timeout_seconds
        while remaining := deadline - time.monotonic():
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                break
            response = _find_response(line, request_id=2)
            if response is None:
                continue
            if "error" in response:
                raise AppServerError(f"Codex app-server returned an error: {response['error']}")
            result = response.get("result")
            if isinstance(result, dict):
                return result
            raise AppServerError(f"Codex app-server returned an invalid result for {method}")
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    stderr = process.stderr.read().strip()
    detail = f" stderr: {stderr}" if stderr else ""
    raise AppServerError(f"Codex app-server did not return {method} (exit {process.returncode}).{detail}")


def _find_response(output: str, *, request_id: int) -> dict[str, Any] | None:
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    return None


def _read_lines(stream: Any, destination: queue.Queue[str | None]) -> None:
    for line in stream:
        destination.put(line)
    destination.put(None)
