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
`DEFAULT_ADMIT_REQUIRED` — and, since 0.11.0, `CALL_RING_TIMEOUT_SECONDS`,
`CALL_MAX_DURATION_SECONDS`, `CALL_NOTIFY_ON_RING` and `CALL_AUTHORIZER` — are
CTO-facing config axes (capability-config.md §16); the `LIVEKIT_*` / `EGRESS_*`
credentials are tuning knobs for the default provider.

## stapel-video

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| VIDEO_PROVIDER | env | axis: dotted path to a VideoProvider — which vendor carries the calls (SDK imported lazily). | no | stapel_video.providers.livekit.LiveKitProvider |
| SCOPE_PROVIDER | env | dotted path to a ScopeProvider (scope membership for the scope_trusted auto-admit). | no | stapel_video.scope.DefaultScopeProvider |
| LIVE_ROOMS_PROVIDER | env | dotted path to a LiveRoomsProvider — "which calls is this user in right now" for the profile.changed rename. Default reads this module's own tables; a host running its own rooms MUST point this at its adapter (system check stapel_video.E008 refuses to boot otherwise). | no | stapel_video.live_rooms.DefaultLiveRoomsProvider |
| DEFAULT_ACCESS_LEVEL | env | axis: default room access — public \| scope_trusted \| restricted. | no | restricted |
| DEFAULT_ADMIT_REQUIRED | env | axis: whether a fresh room starts with the lobby on. | no | true |
| LIVEKIT_URL | env | LiveKit server URL **this process** reaches the media server on (twirp admin calls). On a host-networked deployment this is not an address a browser can resolve — see LIVEKIT_CLIENT_URL. | no | (empty) |
| LIVEKIT_CLIENT_URL | env | LiveKit signalling URL handed to the **browser** by `POST /calls` (e.g. `wss://example.com/rtc`). Empty falls back to LIVEKIT_URL, which is correct only where both reach the same address; `stapel_video.W007` warns when the fallback would hand out an unreachable one. | no | (empty) |
| LIVEKIT_API_KEY | vault | LiveKit API key (default provider). | no | (empty) |
| LIVEKIT_API_SECRET | vault | LiveKit API secret (default provider). | no | (empty) |
| JOIN_TOKEN_TTL_SECONDS | env | join-token time-to-live (seconds). | no | 3600 |
| EGRESS_S3_ENDPOINT | env | recording-egress object-store endpoint (egress seam, off by default). | no | (empty) |
| EGRESS_S3_BUCKET | env | recording-egress bucket. | no | (empty) |
| EGRESS_S3_ACCESS_KEY | vault | recording-egress access key. | no | (empty) |
| EGRESS_S3_SECRET_KEY | vault | recording-egress secret key. | no | (empty) |
| CALL_RING_TIMEOUT_SECONDS | env | axis: how long a 1:1 call rings before it becomes a missed call. Visible to both parties — the caller's screen counts down against this exact deadline (`expires_at`). | no | 45 |
| CALL_TOKEN_TTL_SECONDS | env | TTL of a call's media token. A ceiling on RECONNECTING, not on the credential: the token is presented again on every full reconnect and nothing re-mints it, so a short value produces calls that connect, run and cannot come back from a tunnel. `POST /calls/{id}/token` re-mints. | no | 3600 |
| CALL_SWEEP_INTERVAL_SECONDS | env | cadence of the call reconciler (`video-call-sweep` / `video_sweep_calls`). Bounds how long a caller stares at a ring that is already over. | no | 10 |
| CALL_MAX_DURATION_SECONDS | env | axis: hard stop on an accepted call, for the one nobody hangs up. The media server's idle timeout never fires for it — the room is not empty. | no | 7200 |
| CALL_CONNECT_GRACE_SECONDS | env | how long after `accept` the reconciler leaves a call alone. Too low and calls hang up by themselves (the room is still empty while both browsers dial); too high and a genuinely dead call is metered for that long. | no | 30 |
| CALL_ROOM_EMPTY_TIMEOUT_SECONDS | env | `empty_timeout` the provider is given for a call room. | no | 60 |
| CALL_AUTHORIZER | env | axis + seam: dotted path to `(request, *, caller, callee_id, thread_key) -> bool` — who may make whose phone ring. Default requires both parties to be participants of the call's thread; fail-closed on every uncertainty. A user id is not a phone number. | no | stapel_video.calls.authorize.thread_participants |
| CALL_PARTICIPANTS_FUNCTION | env | comm Function NAME the default authorizer asks for conversation membership. Empty makes the default authorizer refuse every call rather than allow them. | no | chat.conversation_participants |
| CALL_THREAD_MESSAGE_FUNCTION | env | comm Function NAME a finished call writes its system line through (`video.call.ended:<seconds>` / `.missed` / `.declined`). Empty turns the line off. | no | chat.post_system_message |
| CALL_NOTIFY_ON_RING | env | axis: whether a ring also requests a push notification (`call.incoming`). A missed call is notified either way. Since 0.11.1 the ring push is suppressed for a callee the fleet says is already watching (`CALL_PRESENCE_FUNCTION`). | no | true |
| CALL_PRESENCE_FUNCTION | env | comm Function NAME asked "is this person watching right now" before the ring push, so a browser already showing the overlay does not also buzz the phone in the same hand. Provided by stapel-realtime 0.2.0. Unreachable, erroring, or empty means the push goes out unconditionally — an unheard call costs more than a redundant banner. | no | realtime.is_live |
| WEBHOOK_HANDLERS | env | merge registry over `webhooks.BUILTIN_WEBHOOK_HANDLERS`: `{provider event type: dotted path \| None to remove}`. Never read from the environment — a stray string would be iterated as characters. | no | {} (builtins: egress_ended, egress_updated, participant_joined, participant_left, room_finished) |
| PRESENCE_SWEEP_INTERVAL_SECONDS | env | how often the reconciler compares open presence spans against the media server's roster. Also the worst-case over-count of a lost `participant_left`. | no | 60 |
| PRESENCE_SPAN_RETENTION_DAYS | env | age at which `video_purge_spans` deletes a ParticipantSpan. `None` = keep forever (stated, not drifted into). | no | 400 |
| PRESENCE_PURGE_SCHEDULE | env | crontab kwargs for the retention purge in `get_video_beat_schedule()`. Never read from the environment (a string is not a schedule). | no | {"hour": 4, "minute": 10} |
| USAGE_MANDATE | env | capability a caller must hold **in the scope they ask about** to read `GET /video/api/v1/scopes/{scope_key}/usage/`, resolved through the workspaces access registry (`workspaces.check_capability`). The host registers it on the roles it means to grant it to, next to its own `members.invite`. | no | video.usage.read |
| USAGE_AUTHORIZER | env | dotted path to `(request, scope_key) -> bool`, consulted INSTEAD of the registry in a deployment that cannot ask about mandates at all (no `stapel_workspaces`, no routed seam). The default is staff-only: "nothing here knows who owns this partition" degrades to the operator, not to everyone. System check `stapel_video.E011` refuses to boot on a broken path. | no | stapel_video.usage.staff_only_authorizer |
| USAGE_THROTTLE | env | DRF rate for the `video-scope-usage` throttle scope, read from this namespace — a library does not own the project's `DEFAULT_THROTTLE_RATES`. `None` disables throttling. | no | 60/min |
