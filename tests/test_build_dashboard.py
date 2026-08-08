from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_builder():
    path = Path(__file__).resolve().parents[1] / "build_dashboard.py"
    spec = importlib.util.spec_from_file_location("build_dashboard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_report_declares_utf8_before_unicode_text(tmp_path) -> None:
    module = load_builder()
    history = tmp_path / "2026-05.jsonl"
    history.write_text(
        json.dumps({
            "t": "2026-05-04T00:00:00Z",
            "quota_status": "ok",
            "session": 5,
            "weekly": 20,
            "max": 20,
            "tokens_7d": 700_000,
        }) + "\n",
        encoding="utf-8",
    )

    assert module.main([str(history)]) == 0
    html = history.with_suffix(".html").read_text(encoding="utf-8")

    assert html.startswith('<!doctype html>\n<meta charset="utf-8">')
    assert "Codex Quota — May 2026" in html


def test_weekly_only_report_marks_session_unavailable() -> None:
    module = load_builder()
    rows = [{
        "t": "2026-05-04T00:00:00Z",
        "_dt": module.parse_ts("2026-05-04T00:00:00Z"),
        "quota_status": "ok",
        "session": None,
        "weekly": 20,
        "max": 20,
        "tokens_7d": 700_000,
    }]

    payload = module.build_payload(rows, 2026, 5)
    session_kpi = next(kpi for kpi in payload["kpis"] if kpi["key"] == "sessionPeak")

    assert payload["meta"]["hasSession"] is False
    assert session_kpi["value"] is None
    assert session_kpi["note"] == {"type": "sessionUnavailable"}
    assert "not exposed by Codex" in module.render(payload)
