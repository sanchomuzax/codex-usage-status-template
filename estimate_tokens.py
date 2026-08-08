#!/usr/bin/env python3
"""Approximate 7-day Codex token usage from local rollout logs."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WINDOW_DAYS = 7
LOG_ROOT = Path.home() / ".codex" / "sessions"
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def parse_timestamp(raw):
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def session_total(path, cutoff):
    latest = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"token_count"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (parse_timestamp(record.get("timestamp")) or cutoff) < cutoff:
                    continue
                payload = record.get("payload") or {}
                usage = (payload.get("info") or {}).get("total_token_usage")
                if payload.get("type") == "token_count" and isinstance(usage, dict):
                    latest = usage
    except OSError as error:
        print(f"warning: cannot read {path}: {error}", file=sys.stderr)
    return latest


def collect(cutoff):
    totals = {field: 0 for field in FIELDS}
    sessions = 0
    for path in sorted(LOG_ROOT.rglob("*.jsonl")):
        usage = session_total(path, cutoff)
        if not usage:
            continue
        sessions += 1
        for field in FIELDS:
            value = usage.get(field)
            if isinstance(value, int):
                totals[field] += value
    return totals, sessions


def main():
    if not LOG_ROOT.is_dir():
        json.dump({"error": f"session log directory not found: {LOG_ROOT}"}, sys.stdout)
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    totals, sessions = collect(cutoff)
    uncached_input = max(totals["input_tokens"] - totals["cached_input_tokens"], 0)
    estimated = uncached_input + totals["output_tokens"]
    json.dump({
        "window_days": WINDOW_DAYS,
        "sessions_scanned": sessions,
        "input_tokens": totals["input_tokens"],
        "uncached_input_tokens": uncached_input,
        "cached_input_tokens": totals["cached_input_tokens"],
        "output_tokens": totals["output_tokens"],
        "reasoning_output_tokens": totals["reasoning_output_tokens"],
        "estimated_tokens_7d": estimated,
        "estimated_tokens_7d_including_cache": totals["input_tokens"] + totals["output_tokens"],
    }, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
