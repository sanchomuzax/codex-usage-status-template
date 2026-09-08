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


def test_the_line_breaks_where_the_active_account_changes(tmp_path) -> None:
    """Two subscriptions are two quotas; joining them draws a cliff that is not
    a change in consumption. The series marks the switch so the chart breaks."""
    module = load_builder()
    history = tmp_path / "2026-05.jsonl"
    history.write_text(
        "\n".join(
            json.dumps({
                "t": stamp,
                "quota_status": "ok",
                "session": 5,
                "weekly": weekly,
                "max": weekly,
                "tokens_7d": 1,
                "account": account,
            })
            for stamp, weekly, account in (
                ("2026-05-04T00:00:00Z", 67, "account-a"),
                ("2026-05-04T00:05:00Z", 67, "account-a"),
                ("2026-05-04T00:10:00Z", 53, "account-b"),
                ("2026-05-04T00:15:00Z", 53, "account-b"),
            )
        ) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.html"

    module.main([str(history), "-o", str(out)])
    html = out.read_text(encoding="utf-8")
    payload = html.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
    series = json.loads(payload)["series"]

    assert [point.get("b") for point in series] == [0, 0, 1, 0]
    assert [point.get("a") for point in series] == ["account-a", "account-a", "account-b", "account-b"]
