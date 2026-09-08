#!/usr/bin/env python3
"""Read real token usage from the Codex account, without spending tokens.

``account/usage/read`` is the server's own accounting -- the same figures the
Codex ``/usage`` view shows. Like the rate-limit method it starts no model turn,
so reading it costs nothing. It replaces the previous approach of summing local
rollout logs, which could only see work done through the Codex CLI on this
machine and therefore reported a figure hundreds of times too small whenever
the account was driven by anything else.
"""

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from codex_usage_status.accounts import active_account
from codex_usage_status.collector import AppServerError, fetch_usage

WINDOW_DAYS = 7


def parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def daily_totals(payload):
    """Map each dated bucket to its token count, skipping anything unreadable."""
    buckets = payload.get("dailyUsageBuckets")
    if not isinstance(buckets, list):
        return None

    totals = {}
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        day = parse_day(bucket.get("startDate"))
        tokens = bucket.get("tokens")
        if day is None or not isinstance(tokens, (int, float)):
            continue
        totals[day] = totals.get(day, 0) + tokens
    return totals


def normalize(payload, *, today=None):
    """Summarize the account's token usage over the trailing seven days.

    The window is seven *calendar days*, not the seven most recent buckets: the
    server only emits a bucket for a day with usage, so counting entries would
    quietly reach back weeks across any break in the work.
    """
    today = today or datetime.now(UTC).date()
    summary = payload.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    totals = daily_totals(payload)

    if totals is None:
        # No buckets at all means the server told us nothing about consumption.
        # That is unknown, not zero.
        window_total = None
        today_total = None
        days_counted = None
    else:
        first_day = today - timedelta(days=WINDOW_DAYS - 1)
        inside = {day: count for day, count in totals.items() if first_day <= day <= today}
        window_total = sum(inside.values())
        today_total = totals.get(today, 0)
        days_counted = len(inside)

    def figure(key):
        value = summary.get(key)
        return value if isinstance(value, (int, float)) else None

    return {
        "window_days": WINDOW_DAYS,
        "days_counted": days_counted,
        "tokens_7d": window_total,
        "tokens_today": today_total,
        "tokens_lifetime": figure("lifetimeTokens"),
        "peak_daily_tokens": figure("peakDailyTokens"),
        "current_streak_days": figure("currentStreakDays"),
        "longest_streak_days": figure("longestStreakDays"),
        "longest_turn_seconds": figure("longestRunningTurnSec"),
        "source": "account/usage/read",
    }


def main():
    try:
        result = normalize(fetch_usage())
        result["account"] = active_account()
        json.dump(result, sys.stdout)
    except (AppServerError, ValueError, OSError) as error:
        json.dump({"error": str(error)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
