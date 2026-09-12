from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_budget_check():
    path = Path(__file__).resolve().parents[1] / "budget_check.py"
    spec = importlib.util.spec_from_file_location("budget_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_brief_output_labels_unexposed_session(monkeypatch, capsys) -> None:
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": None,
        "weekly_percent_used": 20,
        "weekly_resets_at": None,
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--brief"])

    assert module.main() == 0
    output = capsys.readouterr().out

    assert "session — (not exposed)" in output
    assert "weekly 20%" in output


def test_cached_reading_from_another_account_is_not_reported_as_current(monkeypatch) -> None:
    """A switched account makes a cached reading describe someone else's quota.

    The account balancer re-points ~/.codex/auth.json between two subscriptions.
    A status.json written before a switch carries the other account's figures,
    and reporting them as the current window is exactly how a stale reading
    misleads -- the same failure the expired-session guard exists for.
    """
    module = load_budget_check()
    monkeypatch.setattr(module, "active_account", lambda: "account-b")

    result = module.evaluate(
        {
            "session_percent_used": 10,
            "weekly_percent_used": 20,
            "weekly_resets_at": None,
            "account": "account-a",
        },
        source="cached",
    )

    assert result["verdict"] == "UNKNOWN"
    assert any("account" in reason for reason in result["reasons"])
    assert result["weekly_percent_used"] is None
    assert result["session_percent_used"] is None


def test_cached_reading_from_the_same_account_still_counts(monkeypatch) -> None:
    module = load_budget_check()
    monkeypatch.setattr(module, "active_account", lambda: "account-b")

    result = module.evaluate(
        {
            "session_percent_used": 10,
            "weekly_percent_used": 20,
            "weekly_resets_at": None,
            "account": "account-b",
        },
        source="cached",
    )

    assert result["weekly_percent_used"] == 20


def test_the_brief_names_the_account_the_verdict_is_about(monkeypatch, capsys) -> None:
    """A reader must never have to assume the verdict is about their own quota.

    The collector reports whichever subscription ~/.codex/auth.json points at.
    A Codex desktop app keeps its own session under ~/.config/Codex, so an agent
    running there can be working in one subscription while this verdict
    describes another -- silently, because the two look identical.
    """
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 7,
        "weekly_percent_used": 13,
        "weekly_resets_at": None,
        "account": "account-b",
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--brief"])

    module.main()

    assert "account-b" in capsys.readouterr().out


def test_an_unreported_account_is_not_invented(monkeypatch, capsys) -> None:
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 7,
        "weekly_percent_used": 13,
        "weekly_resets_at": None,
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--brief"])

    module.main()
    output = capsys.readouterr().out

    assert "acct" not in output
    assert "weekly 13%" in output
