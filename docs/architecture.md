# Architecture

The monitor consists of five separable layers:

| Layer | Responsibility |
| --- | --- |
| Collector | Start `codex app-server` and call `account/rateLimits/read` without starting a model turn. |
| Normalizer | Convert the app-server response to stable session/weekly fields and reconcile it with the newest server-provided session snapshot when the account snapshot is stale. |
| Policy | Turn normalized status into `GO`, `CAUTION`, or `STOP`. |
| Historian | Maintain the current state and append timestamped readings. |
| Publisher | Commit/push meaningful changes and build the offline dashboard. |

The collector deliberately stays behind the Codex CLI boundary. It does not
read credential files or call an internal remote endpoint directly: Codex owns
authentication and token refresh. The JSON-RPC request contains no
`turn/start`, so collecting quota does not invoke a model or consume tokens.

Missing windows remain `null` and are labelled unavailable in the dashboard and
orchestrator output — the monitor never invents a zero. Both the weekly and the
optional 300-minute/5-hour session window are supported, and either may be
absent from a given account shape.

The account method reports several limit *groups* in `rateLimitsByLimitId`, and
serialises them from an unordered map, so their order changes between calls. The
`codex` group owns every headline field (`session_*`, `weekly_*`,
`rate_limit_reached_type`); other groups — `base_model_inference`, for one —
appear in `limits` and still drive `max_percent_used`, but never supply a
headline figure while the `codex` group is present, because an unused group
reports 0% and that is indistinguishable from a fresh window. `session_group`
and `weekly_group` record which group each figure came from. Rows in `limits`
are sorted, so the published file does not churn with the map order.

A second identity travels with every reading: the Codex account. An account
balancer may re-point `~/.codex/auth.json` between subscriptions, and the
app-server authenticates as whichever one is active, so the monitor follows a
switch on its own. The subscriptions' windows are independent, though, and a
figure from one says nothing about the other. `active_account()` reads
`~/.codex/current` (falling back to the `auth.json` symlink target) and the
name is published as `account` in both `status.json` and the history samples.
The dashboard breaks its lines where the account changes rather than drawing a
cliff that is not a change in consumption, a switch is on its own enough reason
to publish a reading, and `budget_check.py` discards a cached reading taken
under a different account instead of reporting it as the current window.

One consequence is easy to get backwards when reading the output. A quota
window belongs to the *account*, not to this machine: any other machine,
session or person signed in to the same subscription draws on the same window.
So a figure for an account that is idle here can still move, and "not active
locally" is not the same as "not consuming". Observed on 2026-09-09: while the
local agent ran on one subscription, the other one's weekly figure rose from
18% to 23% and its 5-hour window reached 34%, entirely from use elsewhere.
Nothing in this repository should assume an idle account's numbers stand
still -- and neither should anyone reading a flat line and concluding the
collector has stalled. Recent model responses also carry a
server-side `rate_limits` snapshot in local rollout logs. If the account method
temporarily returns a lower value for the same duration and reset window, the
normalizer retains the higher observed utilization. The app-server result
remains the primary and fallback source.

Token counts come from `account/usage/read`, a second read-only method on the
same app-server: the server's own accounting, the figures behind the Codex
`/usage` view. It replaced an earlier estimate that summed local
`~/.codex/sessions/**/*.jsonl` rollout logs -- that could only see work driven
through the Codex CLI on this machine, so once the account was driven by
anything else it reported a figure hundreds of times too small while looking
perfectly stable. The window is seven calendar days, not the seven most recent
buckets: the server emits a bucket only for a day with usage, so counting
entries would reach back weeks across a break. Token counts are not a
substitute for the quota percentages; different models consume quota at
different rates.
