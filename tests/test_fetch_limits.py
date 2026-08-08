from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module(name: str):
    path = Path(__file__).resolve().parents[1] / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_maps_five_hour_and_weekly_windows() -> None:
    module = load_module("fetch_limits.py")
    payload = {
        "rateLimits": {
            "limitId": "codex",
            "planType": "plus",
            "primary": {"usedPercent": 21, "windowDurationMins": 300, "resetsAt": 1},
            "secondary": {"usedPercent": 34, "windowDurationMins": 10080, "resetsAt": 2},
            "rateLimitReachedType": None,
        },
        "rateLimitResetCredits": {"availableCount": 1, "credits": [{"id": "opaque"}]},
    }

    result = module.normalize(payload)

    assert result["session_percent_used"] == 21
    assert result["weekly_percent_used"] == 34
    assert result["max_percent_used"] == 34
    assert result["available_reset_credits"] == 1
    assert "opaque" not in str(result)


def test_normalize_accepts_weekly_only_snapshot() -> None:
    module = load_module("fetch_limits.py")
    result = module.normalize({
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 15, "windowDurationMins": 10080, "resetsAt": 2},
        }
    })

    assert result["session_percent_used"] is None
    assert result["weekly_percent_used"] == 15


def test_fresh_session_snapshot_corrects_zero_account_snapshot() -> None:
    module = load_module("fetch_limits.py")
    account = {
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1004},
        }
    }
    session = {
        "limit_id": "codex",
        "primary": {"used_percent": 24, "window_minutes": 10080, "resets_at": 1000},
    }

    result = module.normalize(account, session)

    assert result["weekly_percent_used"] == 24
    assert result["max_percent_used"] == 24
