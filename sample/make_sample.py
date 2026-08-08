#!/usr/bin/env python3
"""Generate a synthetic demo history file for the dashboard.

This is a *sample* dataset shipped with the template so newcomers can run
``build_dashboard.py`` and see a populated dashboard before collecting any of
their own data. It is entirely made up: a seeded simulation, not real usage.

    python3 sample/make_sample.py            # writes sample/2026-05.jsonl

The shape (evening-weighted bursts, weekly resets, and one auth-error gap)
mirrors a plausible weekly-only Codex response without reproducing any actual
figures. The optional 5-hour session window is deliberately ``null`` because
Codex does not currently expose it; the monitor still supports it if it returns.
"""
from __future__ import annotations

import json
import hashlib
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260504
EXPECTED_SHA256 = "509b96716e1a87baa6df1145b5ca672ef8c9d5f6ee7cd2d4cfe041ac3794db09"
START = datetime(2026, 5, 4, 0, 0, tzinfo=timezone.utc)
DAYS = 14
STEP_MIN = 5
WEEKLY_WIN_MIN = 7 * 1440      # 7-day weekly window

# Engineered, clearly-labelled synthetic event:
ERROR_START = START + timedelta(days=9, hours=12, minutes=20)
ERROR_END = START + timedelta(days=9, hours=15, minutes=55)


def diurnal(hour: float) -> float:
    """Activity weight over the day: quiet nights, a late-afternoon/evening peak."""
    evening = math.exp(-((hour - 18) ** 2) / (2 * 3.1 ** 2))
    morning = 0.30 * math.exp(-((hour - 9) ** 2) / (2 * 1.7 ** 2))
    return evening + morning


def main() -> int:
    rng = random.Random(SEED)
    out = Path(__file__).with_name("2026-05.jsonl")

    n = DAYS * 24 * 60 // STEP_MIN
    weekly_acc = 0.0
    prev_wwin = -1
    lines = []

    for i in range(n):
        t = START + timedelta(minutes=i * STEP_MIN)
        mins = i * STEP_MIN
        day = (t - START).days
        hour = t.hour + t.minute / 60.0

        wwin = mins // WEEKLY_WIN_MIN
        if wwin != prev_wwin:
            weekly_acc = 0.0
            prev_wwin = wwin

        # per-day energy so some days are busier than others
        day_factor = 0.55 + 0.4 * ((rng.random() + math.sin(day)) % 1.0)

        weight = diurnal(hour)
        # is the agent doing anything this 5-minute slot?
        active = rng.random() < (0.08 + 0.60 * min(weight, 1.0))
        burst = weight * day_factor * (rng.uniform(1.4, 2.9) if active else 0.0)

        # Weekly-only sample: enough movement to show a useful graph and a
        # warning band, without inventing a currently absent session window.
        weekly_acc = min(84.0, weekly_acc + burst * rng.uniform(0.11, 0.16))

        # token estimate: a separate, lower band than the author's (~0.8-1.15M)
        tokens = int(
            860_000
            + 150_000 * (0.5 + 0.5 * math.sin((mins / WEEKLY_WIN_MIN) * math.tau))
            + rng.randint(-24_000, 24_000)
        )

        in_error = ERROR_START <= t <= ERROR_END
        if in_error:
            session = weekly = mx = None
            status = "error"
        else:
            session = None
            weekly = round(weekly_acc)
            mx = weekly
            if mx >= 75:
                status = "warning"
            else:
                status = "ok"

        lines.append(json.dumps({
            "t": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "quota_status": status,
            "session": session,
            "weekly": weekly,
            "max": mx,
            "tokens_7d": tokens,
        }))

    payload = ("\n".join(lines) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"synthetic fixture changed unexpectedly: {digest}")
    out.write_bytes(payload)
    print(f"wrote {len(lines)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
