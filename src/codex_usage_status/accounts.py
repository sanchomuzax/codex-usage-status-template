"""Identify which Codex account the CLI is currently authenticated as.

An account balancer may re-point ``~/.codex/auth.json`` between subscriptions
while the monitor is running. Every quota figure belongs to whichever account
was active when it was read, so the name has to travel with the reading: two
subscriptions have two independent windows, and a figure from one says nothing
about the other.
"""

from __future__ import annotations

import os
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"


def active_account(codex_home: Path | str | None = None) -> str | None:
    """Return the active account's name, or ``None`` when it cannot be told.

    ``~/.codex/current`` is the switcher's own marker. When it is missing, the
    ``auth.json`` symlink still names the account file it points at. Neither is
    guaranteed to exist -- a plain single-account login has no marker and no
    symlink -- and an unknown account must read as unknown, never as a guess.
    """
    home = Path(codex_home) if codex_home is not None else CODEX_HOME

    try:
        name = (home / "current").read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass

    auth = home / "auth.json"
    try:
        if auth.is_symlink():
            return Path(os.readlink(auth)).stem or None
    except OSError:
        pass
    return None
