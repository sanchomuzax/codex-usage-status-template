from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path


def load_estimator():
    path = Path(__file__).resolve().parents[1] / "estimate_tokens.py"
    spec = importlib.util.spec_from_file_location("estimate_tokens", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_session_total_uses_final_cumulative_counter(tmp_path) -> None:
    module = load_estimator()
    path = tmp_path / "rollout.jsonl"
    rows = []
    for input_tokens in (10, 30):
        rows.append(json.dumps({
            "timestamp": "2026-01-01T12:00:00Z",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": 5,
                    "output_tokens": 2,
                    "reasoning_output_tokens": 1,
                }},
            },
        }))
    path.write_text("\n".join(rows), encoding="utf-8")

    result = module.session_total(path, datetime(2026, 1, 1, tzinfo=UTC))

    assert result["input_tokens"] == 30
