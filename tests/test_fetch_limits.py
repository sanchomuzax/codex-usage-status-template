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


def _two_group_payload(order: tuple[str, ...]) -> dict:
    """Account payload with a second weekly limit group, in a chosen key order.

    The app-server serialises ``rateLimitsByLimitId`` from an unordered map, so
    the same account state arrives with the groups in either order.
    """
    groups = {
        "codex": {
            "limitId": "codex",
            "planType": "plus",
            "primary": {"usedPercent": 18, "windowDurationMins": 300, "resetsAt": 1000},
            "secondary": {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 2000},
        },
        "base_model_inference": {
            "limitId": "base_model_inference",
            "primary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 3000},
        },
    }
    return {"rateLimitsByLimitId": {name: groups[name] for name in order}}


def test_weekly_ignores_unrelated_group_regardless_of_key_order() -> None:
    module = load_module("fetch_limits.py")

    for order in (("codex", "base_model_inference"), ("base_model_inference", "codex")):
        result = module.normalize(_two_group_payload(order))

        assert result["weekly_percent_used"] == 12, order
        assert result["weekly_resets_at"] == module.iso_timestamp(2000), order
        assert result["session_percent_used"] == 18, order


def test_limits_order_is_stable_across_key_orders() -> None:
    module = load_module("fetch_limits.py")

    def shape(payload):
        return [(row["group"], row["window_duration_minutes"]) for row in module.normalize(payload)["limits"]]

    assert shape(_two_group_payload(("codex", "base_model_inference"))) == shape(
        _two_group_payload(("base_model_inference", "codex"))
    )


def test_weekly_is_null_not_another_groups_figure_when_codex_has_no_weekly() -> None:
    module = load_module("fetch_limits.py")

    result = module.normalize({
        "rateLimitsByLimitId": {
            "base_model_inference": {
                "limitId": "base_model_inference",
                "primary": {"usedPercent": 7, "windowDurationMins": 10080, "resetsAt": 3000},
            },
            "codex": {
                "limitId": "codex",
                "primary": {"usedPercent": 18, "windowDurationMins": 300, "resetsAt": 1000},
            },
        }
    })

    assert result["weekly_percent_used"] is None
    assert result["weekly_group"] is None
    assert result["session_percent_used"] == 18
    assert result["session_group"] == "codex"
    # The unrelated group is still published and still drives the worst-case.
    assert result["max_percent_used"] == 18
    assert [row["group"] for row in result["limits"]] == ["codex", "base_model_inference"]


def test_headline_falls_back_when_the_codex_group_is_absent_entirely() -> None:
    module = load_module("fetch_limits.py")

    result = module.normalize({
        "rateLimitsByLimitId": {
            "base_model_inference": {
                "limitId": "base_model_inference",
                "primary": {"usedPercent": 7, "windowDurationMins": 10080, "resetsAt": 3000},
            },
        }
    })

    assert result["weekly_percent_used"] == 7
    assert result["weekly_group"] == "base_model_inference"


def test_limits_order_puts_the_primary_group_first() -> None:
    module = load_module("fetch_limits.py")

    result = module.normalize(_two_group_payload(("base_model_inference", "codex")))

    assert [(row["group"], row["window_duration_minutes"]) for row in result["limits"]] == [
        ("codex", 300),
        ("codex", 10080),
        ("base_model_inference", 10080),
    ]


def test_plan_and_reached_type_come_from_the_primary_group() -> None:
    module = load_module("fetch_limits.py")

    result = module.normalize({
        "rateLimitsByLimitId": {
            "base_model_inference": {
                "planType": "unrelated",
                "rateLimitReachedType": "base_hit",
                "primary": {"usedPercent": 0, "windowDurationMins": 10080},
            },
            "codex": {
                "planType": "plus",
                "rateLimitReachedType": None,
                "primary": {"usedPercent": 12, "windowDurationMins": 10080},
            },
        }
    })

    assert result["subscription_type"] == "plus"
    assert result["rate_limit_reached_type"] is None


def test_max_percent_used_spans_every_group() -> None:
    module = load_module("fetch_limits.py")

    result = module.normalize({
        "rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 12, "windowDurationMins": 10080}},
            "base_model_inference": {"primary": {"usedPercent": 95, "windowDurationMins": 10080}},
        }
    })

    assert result["weekly_percent_used"] == 12
    assert result["max_percent_used"] == 95


def test_a_non_numeric_window_duration_does_not_break_the_reading() -> None:
    module = load_module("fetch_limits.py")

    result = module.normalize({
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {"usedPercent": 12, "windowDurationMins": "10080"},
                "secondary": {"usedPercent": 18, "windowDurationMins": 300},
            },
        }
    })

    assert result["session_percent_used"] == 18
    assert result["max_percent_used"] == 18


def test_a_session_snapshot_group_is_ranked_not_merely_appended() -> None:
    """The sort must order ``limits``, not the order rows happen to be built in.

    Rows from the session log are appended after the account rows, so a Codex
    group that arrives only from the session log lands last unless it is sorted.
    """
    module = load_module("fetch_limits.py")

    result = module.normalize(
        {
            "rateLimitsByLimitId": {
                "base_model_inference": {
                    "primary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 3000},
                },
            }
        },
        {
            "limit_id": "codex",
            "primary": {"used_percent": 12, "window_minutes": 10080, "resets_at": 2000},
        },
    )

    assert [row["group"] for row in result["limits"]] == ["codex", "base_model_inference"]
    assert result["weekly_percent_used"] == 12
    assert result["weekly_group"] == "codex"


def test_active_account_reads_the_current_marker(tmp_path) -> None:
    module = load_module("fetch_limits.py")
    (tmp_path / "current").write_text("account-b\n", encoding="utf-8")

    assert module.active_account(tmp_path) == "account-b"


def test_active_account_falls_back_to_the_auth_symlink(tmp_path) -> None:
    module = load_module("fetch_limits.py")
    accounts = tmp_path / "accounts"
    accounts.mkdir()
    (accounts / "account-a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "auth.json").symlink_to(accounts / "account-a.json")

    assert module.active_account(tmp_path) == "account-a"


def test_active_account_is_none_when_nothing_identifies_it(tmp_path) -> None:
    module = load_module("fetch_limits.py")
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    assert module.active_account(tmp_path) is None


def _account_payload(weekly_percent, resets_at):
    return {"rateLimitsByLimitId": {"codex": {
        "limitId": "codex",
        "primary": {"usedPercent": 14, "windowDurationMins": 300, "resetsAt": 1000},
        "secondary": {"usedPercent": weekly_percent, "windowDurationMins": 10080, "resetsAt": resets_at},
    }}}


def test_a_session_snapshot_never_replaces_a_different_window() -> None:
    """A snapshot describing another window is not a fresher reading of this one.

    Ranking by "later resetsAt wins" treated a stale local rollout log as newer
    than the live account read, and pinned the published weekly figure to a
    12-hour-old value from a different account.
    """
    module = load_module("fetch_limits.py")

    result = module.normalize(
        _account_payload(8, 1789448326),
        {"limit_id": "codex",
         "secondary": {"used_percent": 13.0, "window_minutes": 10080, "resets_at": 1789500000}},
    )

    assert result["weekly_percent_used"] == 8


def test_the_same_window_is_recognised_despite_minutes_of_reset_drift() -> None:
    """The server's reported reset time drifts between reads, so a tolerance of
    seconds splits one window into two and defeats the correction entirely."""
    module = load_module("fetch_limits.py")

    result = module.normalize(
        _account_payload(0, 1789448326),
        {"limit_id": "codex",
         "secondary": {"used_percent": 13.0, "window_minutes": 10080, "resets_at": 1789448488}},
    )

    assert result["weekly_percent_used"] == 13.0


def test_a_stale_session_log_is_not_read_at_all(tmp_path) -> None:
    import os
    import time

    module = load_module("fetch_limits.py")
    day = tmp_path / "2026" / "09" / "08"
    day.mkdir(parents=True)
    log = day / "rollout-old.jsonl"
    log.write_text(
        '{"payload":{"rate_limits":{"limit_id":"codex","secondary":'
        '{"used_percent":13.0,"window_minutes":10080,"resets_at":1}}}}\n',
        encoding="utf-8",
    )
    old = time.time() - 12 * 3600
    os.utime(log, (old, old))

    assert module.latest_session_rate_limits(tmp_path) is None


def test_a_fresh_session_log_is_still_read(tmp_path) -> None:
    module = load_module("fetch_limits.py")
    day = tmp_path / "2026" / "09" / "09"
    day.mkdir(parents=True)
    (day / "rollout-new.jsonl").write_text(
        '{"payload":{"rate_limits":{"limit_id":"codex","secondary":'
        '{"used_percent":13.0,"window_minutes":10080,"resets_at":1}}}}\n',
        encoding="utf-8",
    )

    snapshot = module.latest_session_rate_limits(tmp_path)

    assert snapshot is not None
    assert snapshot["secondary"]["used_percent"] == 13.0
