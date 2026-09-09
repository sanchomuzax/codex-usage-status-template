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

from codex_usage_status.accounts import active_account
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


# A rollout log is only a corrective for the reading happening right now. Once
# it is older than this it describes a window that has moved on -- and possibly
# a different account, since the logs carry no account name and an account
# balancer can re-point the CLI's auth underneath them.
SESSION_SNAPSHOT_MAX_AGE_SECONDS = 30 * 60

# The server's reported reset time drifts by a couple of minutes between reads,
# so a tolerance of seconds splits one window into two.
SAME_WINDOW_TOLERANCE_SECONDS = 10 * 60


def latest_session_rate_limits(session_root=None):
    """Return the newest *recent* server-provided rate-limit event from Codex logs."""
    root = Path(session_root) if session_root is not None else SESSION_ROOT
    if not root.is_dir():
        return None
    try:
        paths = sorted(root.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    cutoff = datetime.now(UTC).timestamp() - SESSION_SNAPSHOT_MAX_AGE_SECONDS
    for path in paths[:20]:
        try:
            if path.stat().st_mtime < cutoff:
                # Sorted newest first, so everything below this is older too.
                return None
        except OSError:
            continue
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


# The app-server serialises ``rateLimitsByLimitId`` from an unordered map, so
# the same account state arrives with its groups in a different order on nearly
# every call. This tool reports Codex quota, so the Codex group owns the
# headline figures; other groups (``base_model_inference``) still ship inside
# ``limits``, but must never take over ``weekly_percent_used`` just because the
# map happened to yield them first -- an unused group reads 0%, which looks
# exactly like a fresh window.
PRIMARY_GROUP = "codex"


def group_sort_key(row):
    """Rank a limit row. Total and type-safe: a window duration the server sends
    as a string (or omits) must not make sorting raise -- that would take the
    whole reading down, which is worse than any ordering question."""
    group = row.get("group") or ""
    duration = row.get("window_duration_minutes")
    numeric = duration if isinstance(duration, (int, float)) else 0
    return (0 if group == PRIMARY_GROUP else 1, group, numeric, str(duration))


def normalize(payload, session_snapshot=None):
    snapshots = payload.get("rateLimitsByLimitId")
    if not isinstance(snapshots, dict) or not snapshots:
        current = payload.get("rateLimits")
        snapshots = {str((current or {}).get("limitId") or "codex"): current}

    candidates = []
    subscription = None
    # Ranked, not raw map order: ``planType`` is taken from the first group that
    # carries one, so the Codex group has to be asked first for it too.
    ranked = sorted(snapshots.items(), key=lambda item: (0 if item[0] == PRIMARY_GROUP else 1, item[0]))
    reached_by_group = {}
    for limit_id, snapshot in ranked:
        if not isinstance(snapshot, dict):
            continue
        subscription = subscription or snapshot.get("planType")
        reached_by_group[limit_id] = snapshot.get("rateLimitReachedType")
        for slot in ("primary", "secondary"):
            row = normalize_window(snapshot.get(slot), limit_id=limit_id, slot=slot)
            if row:
                candidates.append(row)

    # "Which limit did I hit?" is a headline answer too: report the Codex group's
    # own verdict when that group is present, rather than an unrelated group's.
    if PRIMARY_GROUP in reached_by_group:
        reached = reached_by_group[PRIMARY_GROUP]
    else:
        reached = next((value for value in reached_by_group.values() if value), None)

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
                same_reset = abs((left - right).total_seconds()) <= SAME_WINDOW_TOLERANCE_SECONDS
            except ValueError:
                pass
        # Raise a figure only within the window both rows describe. A row for a
        # *different* window is not a fresher reading of this one, and the
        # account endpoint -- read live, this instant -- stays authoritative.
        # Ranking those by "later resetsAt wins" is what let a stale rollout log
        # pin the published weekly figure to a value from hours earlier.
        if same_reset and row["percent_used"] > previous["percent_used"]:
            merged[key] = row
    limits = sorted(merged.values(), key=group_sort_key)

    # Once the Codex group is present, the headline figures come from it and
    # from nowhere else. Another group's window measures a different resource,
    # and an unused one reads 0% -- indistinguishable from a fresh window. A
    # missing figure is reported as null; the monitor never invents a zero.
    primary_rows = [row for row in limits if row["group"] == PRIMARY_GROUP]
    headline_rows = primary_rows or limits

    def first_row(kind):
        return next((row for row in headline_rows if row["kind"] == kind), None)

    def first(kind, field):
        row = first_row(kind)
        return row.get(field) if row else None

    session_row, weekly_row = first_row("session"), first_row("weekly_all")
    percents = [row["percent_used"] for row in limits]
    reset_summary = payload.get("rateLimitResetCredits")
    return {
        "subscription_type": subscription,
        "limits": limits,
        # Deliberately across every group: the worst window drives quota_status,
        # even when it belongs to a group the headline fields do not report.
        "max_percent_used": max(percents) if percents else None,
        "session_percent_used": first("session", "percent_used"),
        "weekly_percent_used": first("weekly_all", "percent_used"),
        "session_resets_at": first("session", "resets_at"),
        "weekly_resets_at": first("weekly_all", "resets_at"),
        # W2 provenance: which group each headline figure came from, so a
        # renamed limit id shows up in the published file instead of silently
        # redirecting the numbers the way the September ordering bug did.
        "session_group": session_row["group"] if session_row else None,
        "weekly_group": weekly_row["group"] if weekly_row else None,
        "rate_limit_reached_type": reached,
        "available_reset_credits": (
            reset_summary.get("availableCount") if isinstance(reset_summary, dict) else None
        ),
    }


def main():
    try:
        result = normalize(fetch_rate_limits(), latest_session_rate_limits())
        # Which subscription these figures describe. A balancer can re-point the
        # CLI's auth between accounts, and each account has its own windows.
        result["account"] = active_account()
        json.dump(result, sys.stdout)
    except (AppServerError, ValueError, OSError) as error:
        json.dump({"error": str(error)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
