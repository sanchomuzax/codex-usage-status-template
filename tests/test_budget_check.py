from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
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


def write_history(tmp_path, rows):
    (tmp_path / "2026-09.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def peer_row(minutes_ago, account, weekly, session=80):
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"t": stamp, "quota_status": "ok", "session": session, "weekly": weekly,
            "max": max(session, weekly), "tokens_7d": 1, "account": account}


def test_another_accounts_own_earlier_reading_is_used(monkeypatch, capsys, tmp_path) -> None:
    """The collector labels every sample with its account, so the history holds
    real measurements of the other subscription from whenever the CLI was last
    signed in to it. Older than now is not the same as unknown."""
    module = load_budget_check()
    monkeypatch.setattr(module, "HISTORY_DIR", write_history(tmp_path, [
        peer_row(400, "account-b", 5), peer_row(174, "account-b", 12), peer_row(4, "account-a", 2)]))
    monkeypatch.setattr(module, "load_limits", lambda: ({
        "session_percent_used": 12, "weekly_percent_used": 2,
        "weekly_resets_at": None, "account": "account-a"}, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-b", "--brief"])

    code = module.main()
    output = capsys.readouterr().out

    assert "weekly 12%" in output          # the newest reading for that account
    assert "acct account-b" in output
    assert "174m" in output                # never without its age
    assert "session" in output and "12%" not in output.split("session")[1][:12]
    assert code == 1                       # a figure this old is not a green light


def test_a_stale_figure_never_produces_a_green_light(monkeypatch, capsys, tmp_path) -> None:
    module = load_budget_check()
    monkeypatch.setattr(module, "HISTORY_DIR", write_history(tmp_path, [peer_row(120, "account-b", 1)]))
    monkeypatch.setattr(module, "load_limits", lambda: (
        {"weekly_percent_used": 2, "account": "account-a"}, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-b", "--brief"])

    assert module.main() == 1
    assert "CAUTION" in capsys.readouterr().out


def test_a_stale_figure_can_still_stop(monkeypatch, capsys, tmp_path) -> None:
    """Downgrading a green light is caution; upgrading a red one would be
    recklessness. A window that was already exhausted has not emptied since."""
    module = load_budget_check()
    monkeypatch.setattr(module, "HISTORY_DIR", write_history(tmp_path, [peer_row(60, "account-b", 97)]))
    monkeypatch.setattr(module, "load_limits", lambda: (
        {"weekly_percent_used": 2, "account": "account-a"}, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-b", "--brief"])

    assert module.main() == 2
    assert "STOP" in capsys.readouterr().out


def test_beyond_the_horizon_there_is_nothing_to_go_on(monkeypatch, tmp_path) -> None:
    module = load_budget_check()
    monkeypatch.setattr(module, "HISTORY_DIR", write_history(tmp_path, [peer_row(13 * 60, "account-b", 12)]))
    monkeypatch.setattr(module, "load_limits", lambda: (
        {"weekly_percent_used": 2, "account": "account-a"}, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-b", "--brief"])

    assert module.main() == 3


def test_an_account_never_seen_is_unknown(monkeypatch, tmp_path) -> None:
    module = load_budget_check()
    monkeypatch.setattr(module, "HISTORY_DIR", write_history(tmp_path, [peer_row(10, "account-a", 2)]))
    monkeypatch.setattr(module, "load_limits", lambda: (
        {"weekly_percent_used": 2, "account": "account-a"}, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-c", "--brief"])

    assert module.main() == 3


def test_the_last_known_session_figure_is_reported_not_assumed(monkeypatch, capsys, tmp_path) -> None:
    """A gifted reset closes a window before its own deadline, so a reset time
    still in the future proves nothing about whether the window survived. The
    figure is therefore reported as what was last seen, never as what holds
    now -- and it cannot turn the verdict green either way."""
    module = load_budget_check()
    row = peer_row(177, "account-b", 12, session=79)
    row["session_resets_at"] = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(module, "HISTORY_DIR", write_history(tmp_path, [row]))
    monkeypatch.setattr(module, "load_limits", lambda: (
        {"weekly_percent_used": 2, "account": "account-a"}, "live"))
    monkeypatch.setattr(sys, "argv", ["budget_check.py", "--account", "account-b", "--brief"])

    code = module.main()
    output = capsys.readouterr().out

    assert "79%" in output
    assert "may have reset" in output
    assert code == 1
