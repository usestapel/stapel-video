# CONFIG.MD — stapel-video

Config registry for **stapel-video** (`static-scaffold-and-config.md` §2). One
row per configuration key the module reads, its **source** (`env` = the process
environment / the `STAPEL_VIDEO` settings dict; `vault` = a credential that
belongs in the `stapel_core.secrets` provider seam), what it is for, whether it
is required, and its default.

Every key is read through `video_settings` (`stapel_video.conf.AppSettings`,
namespace `STAPEL_VIDEO`). Resolution order per key: `settings.STAPEL_VIDEO`
dict → a flat Django setting of the same name → environment variable → the
default below. `VIDEO_PROVIDER`, `DEFAULT_ACCESS_LEVEL` and
`DEFAULT_ADMIT_REQUIRED` are CTO-facing config axes (capability-config.md §16);
the `LIVEKIT_*` / `EGRESS_*` credentials are tuning knobs for the default
provider.

## stapel-video

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| VIDEO_PROVIDER | env | axis: dotted path to a VideoProvider — which vendor carries the calls (SDK imported lazily). | no | stapel_video.providers.livekit.LiveKitProvider |
| SCOPE_PROVIDER | env | dotted path to a ScopeProvider (scope membership for the scope_trusted auto-admit). | no | stapel_video.scope.DefaultScopeProvider |
| LIVE_ROOMS_PROVIDER | env | dotted path to a LiveRoomsProvider — "which calls is this user in right now" for the profile.changed rename. Default reads this module's own tables; a host running its own rooms MUST point this at its adapter (system check stapel_video.E008 refuses to boot otherwise). | no | stapel_video.live_rooms.DefaultLiveRoomsProvider |
| DEFAULT_ACCESS_LEVEL | env | axis: default room access — public \| scope_trusted \| restricted. | no | restricted |
| DEFAULT_ADMIT_REQUIRED | env | axis: whether a fresh room starts with the lobby on. | no | true |
| LIVEKIT_URL | env | LiveKit server URL (default provider). | no | (empty) |
| LIVEKIT_API_KEY | vault | LiveKit API key (default provider). | no | (empty) |
| LIVEKIT_API_SECRET | vault | LiveKit API secret (default provider). | no | (empty) |
| JOIN_TOKEN_TTL_SECONDS | env | join-token time-to-live (seconds). | no | 3600 |
| EGRESS_S3_ENDPOINT | env | recording-egress object-store endpoint (egress seam, off by default). | no | (empty) |
| EGRESS_S3_BUCKET | env | recording-egress bucket. | no | (empty) |
| EGRESS_S3_ACCESS_KEY | vault | recording-egress access key. | no | (empty) |
| EGRESS_S3_SECRET_KEY | vault | recording-egress secret key. | no | (empty) |
| WEBHOOK_HANDLERS | env | merge registry over `webhooks.BUILTIN_WEBHOOK_HANDLERS`: `{provider event type: dotted path \| None to remove}`. Never read from the environment — a stray string would be iterated as characters. | no | {} (builtins: egress_ended, egress_updated, participant_joined, participant_left) |
| PRESENCE_SWEEP_INTERVAL_SECONDS | env | how often the reconciler compares open presence spans against the media server's roster. Also the worst-case over-count of a lost `participant_left`. | no | 60 |
| PRESENCE_SPAN_RETENTION_DAYS | env | age at which `video_purge_spans` deletes a ParticipantSpan. `None` = keep forever (stated, not drifted into). | no | 400 |
| PRESENCE_PURGE_SCHEDULE | env | crontab kwargs for the retention purge in `get_video_beat_schedule()`. Never read from the environment (a string is not a schedule). | no | {"hour": 4, "minute": 10} |
| USAGE_MANDATE | env | capability a caller must hold **in the scope they ask about** to read `GET /video/api/v1/scopes/{scope_key}/usage/`, resolved through the workspaces access registry (`workspaces.check_capability`). The host registers it on the roles it means to grant it to, next to its own `members.invite`. | no | video.usage.read |
| USAGE_AUTHORIZER | env | dotted path to `(request, scope_key) -> bool`, consulted INSTEAD of the registry in a deployment that cannot ask about mandates at all (no `stapel_workspaces`, no routed seam). The default is staff-only: "nothing here knows who owns this partition" degrades to the operator, not to everyone. System check `stapel_video.E011` refuses to boot on a broken path. | no | stapel_video.usage.staff_only_authorizer |
| USAGE_THROTTLE | env | DRF rate for the `video-scope-usage` throttle scope, read from this namespace — a library does not own the project's `DEFAULT_THROTTLE_RATES`. `None` disables throttling. | no | 60/min |
