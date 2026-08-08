#!/usr/bin/env python3
"""Fetch real Codex subscription quota through the local app-server.

Uses the same account/rateLimits/read request as the Codex TUI. It starts no
model turn, reads no credential file, and therefore consumes no tokens.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION_ROOT = Path.home() / ".codex" / "sessions"
sys.path.insert(0, str(ROOT / "src"))

from codex_usage_status.collector import AppServerError, fetch_rate_limits


def iso_timestamp(value):
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def value(mapping, camel, snake):
    return mapping.get(camel) if camel in mapping else mapping.get(snake)


def normalize_window(window, *, limit_id, slot):
    if not isinstance(window, dict):
        return None
    percent = value(window, "usedPercent", "used_percent")
    duration = value(window, "windowDurationMins", "window_minutes")
    if not isinstance(percent, (int, float)):
        return None
    if duration == 300:
        kind = "session"
    elif duration == 10080:
        kind = "weekly_all"
    else:
        kind = f"window_{duration or slot}"
    return {
        "kind": kind,
        "group": limit_id,
        "percent_used": percent,
        "percent_remaining": 100 - percent,
        "severity": None,
        "resets_at": iso_timestamp(value(window, "resetsAt", "resets_at")),
        "scope": limit_id,
        "is_active": True,
        "window_duration_minutes": duration,
    }


def latest_session_rate_limits():
    """Return the newest server-provided rate-limit event from Codex logs."""
    if not SESSION_ROOT.is_dir():
        return None
    try:
        paths = sorted(SESSION_ROOT.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in paths[:20]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshot = (record.get("payload") or {}).get("rate_limits")
            if isinstance(snapshot, dict):
                return snapshot
    return None


def normalize(payload, session_snapshot=None):
    snapshots = payload.get("rateLimitsByLimitId")
    if not isinstance(snapshots, dict) or not snapshots:
        current = payload.get("rateLimits")
        snapshots = {str((current or {}).get("limitId") or "codex"): current}

    candidates = []
    subscription = None
    reached = None
    for limit_id, snapshot in snapshots.items():
        if not isinstance(snapshot, dict):
            continue
        subscription = subscription or snapshot.get("planType")
        reached = reached or snapshot.get("rateLimitReachedType")
        for slot in ("primary", "secondary"):
            row = normalize_window(snapshot.get(slot), limit_id=limit_id, slot=slot)
            if row:
                candidates.append(row)

    # Model responses carry a fresh server-side rate-limit snapshot. The
    # account endpoint can temporarily return 0% for the same reset window;
    # merge by duration/reset and keep the higher observed utilization.
    if isinstance(session_snapshot, dict):
        limit_id = str(value(session_snapshot, "limitId", "limit_id") or "codex")
        subscription = subscription or value(session_snapshot, "planType", "plan_type")
        reached = reached or value(session_snapshot, "rateLimitReachedType", "rate_limit_reached_type")
        for slot in ("primary", "secondary"):
            row = normalize_window(session_snapshot.get(slot), limit_id=limit_id, slot=slot)
            if row:
                candidates.append(row)

    merged = {}
    for row in candidates:
        key = (row["group"], row["window_duration_minutes"])
        previous = merged.get(key)
        if previous is None:
            merged[key] = row
            continue
        same_reset = previous.get("resets_at") == row.get("resets_at")
        if not same_reset and previous.get("resets_at") and row.get("resets_at"):
            try:
                left = datetime.fromisoformat(previous["resets_at"].replace("Z", "+00:00"))
                right = datetime.fromisoformat(row["resets_at"].replace("Z", "+00:00"))
                same_reset = abs((left - right).total_seconds()) <= 60
            except ValueError:
                pass
        if same_reset and row["percent_used"] > previous["percent_used"]:
            merged[key] = row
        elif not same_reset and (row.get("resets_at") or "") > (previous.get("resets_at") or ""):
            merged[key] = row
    limits = list(merged.values())

    def first(kind, field):
        return next((row.get(field) for row in limits if row["kind"] == kind), None)

    percents = [row["percent_used"] for row in limits]
    reset_summary = payload.get("rateLimitResetCredits")
    return {
        "subscription_type": subscription,
        "limits": limits,
        "max_percent_used": max(percents) if percents else None,
        "session_percent_used": first("session", "percent_used"),
        "weekly_percent_used": first("weekly_all", "percent_used"),
        "session_resets_at": first("session", "resets_at"),
        "weekly_resets_at": first("weekly_all", "resets_at"),
        "rate_limit_reached_type": reached,
        "available_reset_credits": (
            reset_summary.get("availableCount") if isinstance(reset_summary, dict) else None
        ),
    }


def main():
    try:
        json.dump(normalize(fetch_rate_limits(), latest_session_rate_limits()), sys.stdout)
    except (AppServerError, ValueError, OSError) as error:
        json.dump({"error": str(error)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
