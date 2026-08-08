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
