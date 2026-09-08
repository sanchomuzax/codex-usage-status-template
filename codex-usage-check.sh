#!/usr/bin/env bash
#
# Codex usage monitor: reads real subscription quota utilization from the
# Codex app-server account interface, estimates recent token usage from local
# session logs, writes status.json, and pushes it to GitHub.
#
# Designed to be run unattended from cron. It never exits non-zero on failure --
# a failure IS the signal, and gets recorded in status.json.
#
# Usage: codex-usage-check.sh

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS_FILE="${REPO_DIR}/status.json"
LOG_FILE="${REPO_DIR}/run.log"
RUN_PROBE=0

# cron gives a minimal PATH; make sure the usual install locations are visible.
# Append rather than replace: on Windows/Git Bash the existing PATH is where
# git/python live, and dropping it would make scheduled runs fail to find them.
export PATH="${HOME}/.local/bin:${HOME}/bin:/usr/local/bin:/usr/bin:/bin:${PATH}"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"${LOG_FILE}"
}

# Windows normally exposes Python as `python` or `py`, not `python3`. Honour an
# explicit interpreter path first, then choose the first command available.
resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s' "${PYTHON_BIN}"
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' python3
  elif command -v python >/dev/null 2>&1; then
    printf '%s' python
  elif command -v py >/dev/null 2>&1; then
    printf '%s' py
  else
    return 1
  fi
}

PYTHON_BIN="$(resolve_python)" || {
  log "no Python interpreter found (tried PYTHON_BIN, python3, python, py)"
  exit 0
}

# Rebuild the HTML dashboard for the current (still-open) month from its
# history file. Runs only on a meaningful change (mirrors the commit decision),
# so it does not rewrite a ~100 KB file 288 times a day. Pure-stdlib and
# LLM-free (~45 ms). The output history/<month>.html is committed and pushed
# by the publish step below, so the report on GitHub tracks the latest reading.
regenerate_dashboard() {
  local month history_file builder
  builder="${REPO_DIR}/build_dashboard.py"
  [[ -f "${builder}" ]] || return 0
  month="$(date -u +%Y-%m)"
  history_file="${REPO_DIR}/history/${month}.jsonl"
  [[ -f "${history_file}" ]] || return 0
  # The report lands next to its data (history/<month>.html), not the repo root.
  if "${PYTHON_BIN}" "${builder}" "${history_file}" \
       -o "${REPO_DIR}/history/${month}.html" >>"${LOG_FILE}" 2>&1; then
    log "dashboard regenerated (history/${month}.html)"
  else
    log "dashboard regeneration failed (see above)"
  fi
}

# --- 1. real quota ----------------------------------------------------------
# Authoritative source: Codex app-server's account/rateLimits/read method.
# It uses the CLI's managed login, refreshes auth itself and costs no tokens.

fetch_quota() {
  local out
  out="$("${PYTHON_BIN}" "${REPO_DIR}/fetch_limits.py" 2>>"${LOG_FILE}")"
  [[ -n "${out}" ]] && printf '%s' "${out}" || printf '%s' '{"error":"fetch_limits.py produced no output"}'
}

limits_json="$(fetch_quota)"
probe_status="not_run"
probe_error_detail="not needed: quota is read directly through account/rateLimits/read"

# --- 3. token estimate from local logs --------------------------------------

usage_json="$("${PYTHON_BIN}" "${REPO_DIR}/estimate_tokens.py" 2>>"${LOG_FILE}")"
if [[ -z "${usage_json}" ]]; then
  usage_json='{"error":"estimate_tokens.py produced no output"}'
fi

# --- 4. write status.json ---------------------------------------------------

decision="$(STATUS_JSON="${STATUS_FILE}" \
PROBE_STATUS="${probe_status}" \
PROBE_DETAIL="${probe_error_detail}" \
PROBE_ENABLED="${RUN_PROBE}" \
USAGE_JSON="${usage_json}" \
LIMITS_JSON="${limits_json}" \
"${PYTHON_BIN}" - <<'PY'
import json, os
from datetime import datetime, timezone

def load(name):
    try:
        return json.loads(os.environ[name])
    except json.JSONDecodeError as error:
        return {"error": f"unparseable {name}: {error}"}

usage = load("USAGE_JSON")
limits = load("LIMITS_JSON")
quota_error = limits.get("error")
worst = limits.get("max_percent_used")

# quota_status summarizes the authoritative numbers in one word.
if quota_error:
    quota_status = "error"
elif isinstance(worst, (int, float)):
    if worst >= 100:
        quota_status = "exhausted"
    elif worst >= 90:
        quota_status = "critical"
    elif worst >= 75:
        quota_status = "warning"
    else:
        quota_status = "ok"
else:
    quota_status = "unknown"

probe_status = os.environ["PROBE_STATUS"]
if os.environ["PROBE_ENABLED"] != "1":
    # Keep the field meaningful when no live call was made: mirror the quota.
    effective = "rate_limited" if quota_status == "exhausted" else (
        "error" if quota_status == "error" else "ok")
else:
    effective = probe_status

status = {
    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),

    # --- authoritative, from OpenAI's usage endpoint ---
    "quota_status": quota_status,
    "session_percent_used": limits.get("session_percent_used"),
    "weekly_percent_used": limits.get("weekly_percent_used"),
    "max_percent_used": worst,
    "session_resets_at": limits.get("session_resets_at"),
    "weekly_resets_at": limits.get("weekly_resets_at"),
    # Which limit group each headline figure came from. Published so a renamed
    # or re-scoped group is visible here instead of quietly redirecting them.
    "session_group": limits.get("session_group"),
    "weekly_group": limits.get("weekly_group"),
    "limits": limits.get("limits"),
    "subscription_type": limits.get("subscription_type"),
    "extra_usage": limits.get("extra_usage"),
    "rate_limit_reached_type": limits.get("rate_limit_reached_type"),
    "available_reset_credits": limits.get("available_reset_credits"),
    "quota_error_detail": quota_error or "",

    # --- optional live call ---
    "probe_status": effective,
    "probe_error_detail": os.environ["PROBE_DETAIL"],
    "probe_was_live": os.environ["PROBE_ENABLED"] == "1",

    # --- approximate, from local logs ---
    "estimated_tokens_7d": usage.get("estimated_tokens_7d"),
    "usage_breakdown_7d": usage,

    "note": (
        "Percentages are REAL, from Codex app-server account/rateLimits/read "
        "(the same account data used by the Codex TUI). estimated_tokens_7d is a separate "
        "approximation from local session logs on this machine only, and does "
        "not map linearly onto the percentages. When probe_was_live is false, "
        "probe_status is derived from the quota figures rather than a live call. "
        "The optional 5-hour session fields remain null when Codex does not expose "
        "that window; null means unavailable, never zero."
    ),
}

# --- decide whether this reading is worth a commit ---
# At a 5-minute cadence most readings are identical or drift by fractions of a
# percent. Committing every one would bury the interesting changes, so only a
# real movement (or a long silence) gets published.

CHANGE_THRESHOLD = 1          # percentage points
HEARTBEAT_HOURS = 6           # publish at least this often regardless

status_path = os.environ["STATUS_JSON"]
previous = None
try:
    with open(status_path, "r", encoding="utf-8") as handle:
        previous = json.load(handle)
except (OSError, json.JSONDecodeError):
    previous = None


def moved(field):
    """True if a percentage field shifted by at least the threshold."""
    if previous is None:
        return True
    old, new = previous.get(field), status.get(field)
    if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
        return old != new
    return abs(new - old) >= CHANGE_THRESHOLD


reasons = []
if previous is None:
    reasons.append("no previous reading")
else:
    if previous.get("quota_status") != status["quota_status"]:
        reasons.append(f"status {previous.get('quota_status')} -> {status['quota_status']}")
    for field in ("session_percent_used", "weekly_percent_used", "max_percent_used"):
        if moved(field):
            reasons.append(f"{field} {previous.get(field)} -> {status.get(field)}")
    # During active Codex work, publish every five-minute sample even when the
    # server-side quota percentage is rounded and has not moved yet. This keeps
    # the monthly HTML token graph at the same cadence as the JSONL history.
    old_tokens = previous.get("estimated_tokens_7d")
    new_tokens = status.get("estimated_tokens_7d")
    if (isinstance(old_tokens, (int, float))
            and isinstance(new_tokens, (int, float))
            and new_tokens != old_tokens):
        reasons.append(f"estimated_tokens_7d {old_tokens} -> {new_tokens}")
    if bool(previous.get("quota_error_detail")) != bool(status["quota_error_detail"]):
        reasons.append("quota error state changed")
    if previous.get("probe_status") != status["probe_status"]:
        reasons.append(f"probe {previous.get('probe_status')} -> {status['probe_status']}")

    # Heartbeat: never let the published file go stale for too long.
    try:
        stamp = datetime.strptime(previous["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ")
        stamp = stamp.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
        if age_hours >= HEARTBEAT_HOURS:
            reasons.append(f"heartbeat ({age_hours:.1f}h since last publish)")
    except (KeyError, ValueError):
        reasons.append("previous timestamp unreadable")

with open(status_path, "w", encoding="utf-8") as handle:
    json.dump(status, handle, indent=2)
    handle.write("\n")

# --- append the reading to a monthly time series ---
# One compact line per run, so trends stay graphable without bloating any file.

history_dir = os.path.join(os.path.dirname(status_path), "history")
os.makedirs(history_dir, exist_ok=True)
history_path = os.path.join(
    history_dir, datetime.now(timezone.utc).strftime("%Y-%m") + ".jsonl"
)
sample = {
    "t": status["timestamp_utc"],
    "quota_status": status["quota_status"],
    "session": status["session_percent_used"],
    "weekly": status["weekly_percent_used"],
    "max": status["max_percent_used"],
    "tokens_7d": status["estimated_tokens_7d"],
}
try:
    with open(history_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample) + "\n")
except OSError as error:
    print(f"warning: cannot append history: {error}")

# The shell reads this last line to decide whether to publish.
if reasons:
    print("COMMIT " + "; ".join(reasons))
else:
    print("SKIP unchanged")
PY
)"
decision_line="$(printf '%s' "${decision}" | tail -n 1)"

# --- 5. commit and push -----------------------------------------------------
# status.json and the history file are always updated locally; only meaningful
# readings are published, so the repo history stays readable at a 5-minute cadence.

cd "${REPO_DIR}" || { log "cannot cd to ${REPO_DIR}"; exit 0; }

if [[ "${decision_line}" != COMMIT* ]]; then
  log "local update only (${decision_line})"
else
  reason="${decision_line#COMMIT }"

  # A meaningful reading landed -> rebuild the dashboard for the current month
  # and publish it alongside status.json / history so the report on GitHub
  # always reflects the latest reading.
  regenerate_dashboard
  month_dashboard="history/$(date -u +%Y-%m).html"

  summary="$("${PYTHON_BIN}" -c "
import json
d = json.load(open('status.json'))
s = d.get('session_percent_used')
w = d.get('weekly_percent_used')
s_label = f'{s}%' if isinstance(s, (int, float)) else 'unavailable'
w_label = f'{w}%' if isinstance(w, (int, float)) else 'unavailable'
print(f\"{d['quota_status']} session={s_label} weekly={w_label}\")
" 2>/dev/null || echo "update")"

  git add status.json history 2>/dev/null
  [[ -f "${month_dashboard}" ]] && git add "${month_dashboard}" 2>/dev/null
  if [[ -z "$(git diff --cached --name-only)" ]]; then
    log "nothing staged despite decision: ${reason}"
  # [skip ci]: ez a commit ADAT, nem kod — nem kell hozza CI. A GitHub
  # jobonkent EGY TELJES PERCRE kerekit felfele, a windows-lab pedig ketszeresen
  # szamit, tehat egy ~20 masodperces futas ~3 szamlazott percbe kerult; ez a
  # szkript pedig otpercenkent pushol. Merve 2026-09-07: 682 szeptemberi futas,
  # ~2000 szamlazott perc — a havi 2000-es keret 90%-a EBBOL ment el.
  elif git commit -q -m "chore: usage status $(date -u +%Y-%m-%dT%H:%MZ) (${summary}) [skip ci]" -m "${reason}"; then
    if git push -q origin HEAD 2>>"${LOG_FILE}"; then
      log "pushed: ${summary} [${reason}]"
    else
      log "push failed (commit kept locally, will go out next run)"
    fi
  else
    log "commit failed"
  fi
fi

# Keep the log from growing without bound.
if [[ -f "${LOG_FILE}" ]] && [[ "$(wc -l <"${LOG_FILE}")" -gt 2000 ]]; then
  tail -n 500 "${LOG_FILE}" >"${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "${LOG_FILE}"
fi

exit 0
