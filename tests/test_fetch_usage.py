from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[1] / "fetch_usage.py"
    spec = importlib.util.spec_from_file_location("fetch_usage", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAYLOAD = {
    "summary": {
        "lifetimeTokens": 4071371130,
        "peakDailyTokens": 99480842,
        "longestRunningTurnSec": 5611,
        "currentStreakDays": 84,
        "longestStreakDays": 84,
    },
    "dailyUsageBuckets": [
        {"startDate": "2026-08-20", "tokens": 500},        # outside the window
        {"startDate": "2026-09-01", "tokens": 1_000},      # outside by one day
        {"startDate": "2026-09-02", "tokens": 2_000},      # first day inside
        {"startDate": "2026-09-05", "tokens": 3_000},
        {"startDate": "2026-09-08", "tokens": 4_000},      # today
    ],
}


def test_window_is_seven_calendar_days_not_seven_buckets() -> None:
    """Buckets exist only for days with usage, so counting the last seven
    entries would silently reach back weeks whenever the user took a break."""
    module = load_module()

    result = module.normalize(PAYLOAD, today=date(2026, 9, 8))

    assert result["tokens_7d"] == 9_000
    assert result["tokens_today"] == 4_000
    assert result["window_days"] == 7
    assert result["days_counted"] == 3


def test_summary_figures_are_carried_through() -> None:
    module = load_module()

    result = module.normalize(PAYLOAD, today=date(2026, 9, 8))

    assert result["tokens_lifetime"] == 4071371130
    assert result["peak_daily_tokens"] == 99480842
    assert result["current_streak_days"] == 84
    assert result["longest_turn_seconds"] == 5611


def test_a_day_without_usage_reads_zero_today_but_keeps_the_window() -> None:
    module = load_module()

    result = module.normalize(PAYLOAD, today=date(2026, 9, 9))

    assert result["tokens_today"] == 0
    # The window slid past 09-02, so its 2000 tokens drop out.
    assert result["tokens_7d"] == 7_000


def test_an_empty_payload_reports_nothing_rather_than_zero() -> None:
    """No buckets means the server told us nothing; that is not zero usage."""
    module = load_module()

    result = module.normalize({}, today=date(2026, 9, 8))

    assert result["tokens_7d"] is None
    assert result["tokens_today"] is None
    assert result["tokens_lifetime"] is None


def test_unparseable_bucket_dates_are_skipped_not_fatal() -> None:
    module = load_module()

    result = module.normalize(
        {"dailyUsageBuckets": [
            {"startDate": "not-a-date", "tokens": 99},
            {"startDate": "2026-09-08", "tokens": 7},
            {"startDate": "2026-09-07"},
        ]},
        today=date(2026, 9, 8),
    )

    assert result["tokens_7d"] == 7
