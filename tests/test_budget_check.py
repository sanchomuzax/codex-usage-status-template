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


def test_a_reading_for_another_account_is_no_answer_at_all(monkeypatch, capsys) -> None:
    """A quota figure is only valid for the account it was taken from.

    The monitor reads whichever account the Codex CLI is signed in to. A caller
    spending a different subscription -- another client with its own session, a
    tool that re-points the CLI's auth -- would otherwise be handed a confident
    GO about somebody else's quota. There is no general way to obtain the other
    account's figures, so the honest answer is that there is no answer.
    """
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 12,
        "weekly_percent_used": 2,
        "weekly_resets_at": None,
        "account": "account-a",
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-b", "--brief"])

    assert module.main() == 3
    output = capsys.readouterr().out

    assert "UNKNOWN" in output
    assert "account-a" in output and "account-b" in output
    # The figures must not travel with a verdict that does not apply to them.
    assert "session 12%" not in output


def test_the_expected_account_can_come_from_the_environment(monkeypatch) -> None:
    module = load_budget_check()
    monkeypatch.setenv("CODEX_USAGE_EXPECT_ACCOUNT", "account-b")
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 12, "weekly_percent_used": 2,
        "weekly_resets_at": None, "account": "account-a",
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--brief"])

    assert module.main() == 3


def test_a_matching_account_is_answered_normally(monkeypatch, capsys) -> None:
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 12, "weekly_percent_used": 2,
        "weekly_resets_at": None, "account": "account-a",
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-a", "--brief"])

    assert module.main() == 0
    assert "session 12%" in capsys.readouterr().out


def test_an_unnamed_reading_cannot_confirm_the_expected_account(monkeypatch) -> None:
    """A reading from before the collector reported accounts cannot prove it is
    the right one, and an unprovable match is not a match."""
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 12, "weekly_percent_used": 2, "weekly_resets_at": None,
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-a", "--brief"])

    assert module.main() == 3


def test_without_an_expected_account_nothing_changes(monkeypatch) -> None:
    """Anyone using a single account passes no flag and sees the old behaviour."""
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 12, "weekly_percent_used": 2,
        "weekly_resets_at": None, "account": "account-a",
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--brief"])

    assert module.main() == 0


def test_a_withheld_answer_does_not_claim_the_window_was_not_exposed(monkeypatch, capsys) -> None:
    """"not exposed" says the provider withheld the window. Declining to answer
    about another account is a different thing and must not borrow the phrase."""
    module = load_budget_check()
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 12, "weekly_percent_used": 2,
        "weekly_resets_at": None, "account": "account-a",
    }, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-b", "--brief"])

    module.main()

    assert "not exposed" not in capsys.readouterr().out
