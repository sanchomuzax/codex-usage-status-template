from __future__ import annotations

import sys

import pytest

from codex_usage_status.collector import AppServerError, _find_response, fetch_rate_limits


def test_finds_rate_limit_response_among_notifications() -> None:
    output = '\n'.join([
        '{"method":"account/updated","params":{}}',
        '{"id":2,"result":{"rateLimits":{"primary":{"usedPercent":14}}}}',
    ])

    assert _find_response(output, request_id=2) == {
        "id": 2,
        "result": {"rateLimits": {"primary": {"usedPercent": 14}}},
    }


def test_fetches_response_from_app_server_fixture(tmp_path) -> None:
    server = tmp_path / "server.py"
    server.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if request.get('id') == 2:\n"
        "  print(json.dumps({'id': 2, 'result': {'rateLimits': {'primary': {'usedPercent': 14}}}}), flush=True)\n",
        encoding="utf-8",
    )

    assert fetch_rate_limits((sys.executable, str(server))) == {
        "rateLimits": {"primary": {"usedPercent": 14}}
    }


def test_raises_when_no_response_is_returned(tmp_path) -> None:
    server = tmp_path / "silent.py"
    server.write_text("", encoding="utf-8")

    with pytest.raises(AppServerError, match="did not return"):
        fetch_rate_limits((sys.executable, str(server)))
