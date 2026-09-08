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
are sorted, so the published file does not churn with the map order. Recent model responses also carry a
server-side `rate_limits` snapshot in local rollout logs. If the account method
temporarily returns a lower value for the same duration and reset window, the
normalizer retains the higher observed utilization. The app-server result
remains the primary and fallback source.

The token estimate is separate and explicitly approximate. It sums final
cumulative counters from local `~/.codex/sessions/**/*.jsonl` files for the
rolling seven-day window; it is not used as a substitute for server quota.
