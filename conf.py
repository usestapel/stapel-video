"""Settings namespace for stapel-video.

All configuration is read through ``video_settings`` (lazily, at call time) —
never via module-level ``os.getenv`` (values would freeze at import).
Resolution order per key: ``settings.STAPEL_VIDEO`` dict -> flat Django
setting of the same name -> environment variable -> default below.

Dotted-path keys listed in ``import_strings`` are resolved with
``import_string`` — the fork-free escape hatch for swappable behavior.

The extension seams (see MODULE.md):

- ``VIDEO_PROVIDER`` — the video backend behind the ``VideoProvider`` ABC
  (mint token / create room / start-stop egress / parse webhook). The default
  points at the LiveKit implementation (imported lazily, so the dotted path
  resolves even without the ``[livekit]`` extra installed); a host may swap in
  its own backend. Also a CTO-facing **config axis** (which vendor runs calls).
- ``SCOPE_PROVIDER`` — resolves the opaque ``scope_key`` from the request and
  answers "is this user a trusted member of the scope?" (the ``scope_trusted``
  auto-admit decision). Default is a single global scope where every
  authenticated user is a member.
- ``USAGE_AUTHORIZER`` — the fallback answer to "may this caller read this
  scope's usage?" for a deployment with no workspaces to ask. Default:
  staff-only. In a workspace-bearing deployment it is not consulted at all —
  ``USAGE_MANDATE`` goes to the access registry instead.
- ``CALL_AUTHORIZER`` — who may make whose phone ring (1:1 calls, 0.11.0).
  The default requires both parties to be participants of the conversation the
  call hangs off, read through ``CALL_PARTICIPANTS_FUNCTION``, and refuses
  everything it cannot verify. A user id is not a phone number; without this
  the id on a public listing page is a ringer. Also a CTO-facing **config
  axis** (a product decision about reachability), which is why it appears in
  both lists rather than under whichever one was noticed first.
- ``LIVE_ROOMS_PROVIDER`` — answers "which calls is this user in right now?"
  for the ``profile.changed`` subscriber. Default reads this module's own
  tables; a host that adopted the provider seam but kept its own Room model
  points this at a ~20-line adapter over the tables it actually writes.
  ``checks.py`` refuses to boot a deployment where the default cannot hold.

Two more **config axes** (capability-config.md §16) set room defaults at
creation time when the client does not specify them:

- ``DEFAULT_ACCESS_LEVEL`` — public | scope_trusted | restricted.
- ``DEFAULT_ADMIT_REQUIRED`` — whether a fresh room starts with the lobby on.

Three more belong to 1:1 calls, and each is visible to the two people on the
call rather than to an operator:

- ``CALL_RING_TIMEOUT_SECONDS`` — how long a phone rings.
- ``CALL_MAX_DURATION_SECONDS`` — the longest a call may run.
- ``CALL_NOTIFY_ON_RING`` — whether a ring reaches somebody out of the app.

The LiveKit credential keys (``LIVEKIT_*``) are tuning knobs, not axes — they
configure the default provider and are ignored when it is swapped out.
"""
from stapel_core.conf import AppSettings

#: AppSettings-shaped literal dict (capability-config.md §2): a top-level
#: DEFAULTS lets the capabilities.json emitter introspect axis keys/kinds
#: without re-parsing the AppSettings() call.
DEFAULTS = {
    # Dotted path to a VideoProvider — the video backend seam. The default is
    # the LiveKit implementation; its SDK is imported lazily inside the
    # methods that use it, so this path resolves without the [livekit] extra
    # and a host swapping in its own backend never installs it. CTO-facing
    # axis: which vendor actually carries the calls.
    "VIDEO_PROVIDER": "stapel_video.providers.livekit.LiveKitProvider",
    # Dotted path to a ScopeProvider — resolves the opaque scope_key from a
    # request and decides scope membership (the scope_trusted auto-admit).
    # The default is a single global scope (every authenticated user is a
    # member); a host may return e.g. workspace_id and real membership.
    "SCOPE_PROVIDER": "stapel_video.scope.DefaultScopeProvider",
    # Dotted path to a LiveRoomsProvider — "which calls is this user in right
    # now", the one lookup the profile.changed subscriber needs. The default
    # reads this module's own Room/RoomParticipant tables, which is right only
    # for a host that mounts this module's URL surface (its join endpoint is
    # what writes those rows). A host running its own rooms over the
    # VIDEO_PROVIDER seam points this at its own adapter — and is made to,
    # by a system check, rather than discovering it from a stale tile.
    "LIVE_ROOMS_PROVIDER": "stapel_video.live_rooms.DefaultLiveRoomsProvider",
    # Default access level for a room created without an explicit one. Axis
    # (capability-config.md §16): public (anyone with the code joins
    # instantly), scope_trusted (scope members auto-admit, others wait),
    # restricted (everyone but the host waits in the lobby).
    "DEFAULT_ACCESS_LEVEL": "restricted",
    # Default lobby switch for a room created without an explicit one. Axis:
    # True means non-auto-admitted joiners wait for a host to let them in.
    "DEFAULT_ADMIT_REQUIRED": True,
    # ── 1:1 calls (0.11.0) ────────────────────────────────────────────
    # How long a call rings before it is missed. AXIS: this is the product's
    # patience, and it is visible — the front counts down against the
    # server's own deadline (`expires_at`), so changing it here changes what
    # a caller sees.
    "CALL_RING_TIMEOUT_SECONDS": 45,
    # TTL of a call's media token. NOT a short-lived credential by accident:
    # a media token is presented AGAIN on every full reconnect and nothing
    # re-mints it automatically, so a TTL shorter than the longest tolerable
    # call is a call that cannot come back from a tunnel — and the symptom
    # looks like a network fault, not an expiry. The credential's safety
    # comes from what it grants (one room, publish/subscribe only, a room
    # capped at two that ends), not from a short clock. `POST
    # /calls/{id}/token` re-mints for a client that wants a fresh one.
    "CALL_TOKEN_TTL_SECONDS": 3600,
    # How often the reconciler expires overdue rings and closes calls whose
    # room is gone. An interval, not a crontab, for the same reason the
    # presence sweep is one: it is a freshness bound, not a wall-clock event.
    # Correctness of the ANSWER does not depend on it — a ringing call past
    # its deadline reads as missed whether or not this ever runs.
    "CALL_SWEEP_INTERVAL_SECONDS": 10,
    # The hard stop on an accepted call. AXIS. A call nobody hangs up — two
    # phones face down on two tables — is otherwise metered forever, and the
    # media server's own empty timeout does not fire while both are still
    # connected.
    "CALL_MAX_DURATION_SECONDS": 7200,
    # What the media server is told to do with a call room nobody is in.
    "CALL_ROOM_EMPTY_TIMEOUT_SECONDS": 60,
    # How long after `accept` the reconciler leaves a call alone. An accepted
    # call is a promise about two browsers that have not dialled yet: the
    # token was handed over milliseconds ago and the media room is still
    # empty, so a roster read at that moment says "fewer than two" about a
    # call that is starting perfectly normally. Too low and calls hang up by
    # themselves; too high and a genuinely dead call is metered for that long.
    "CALL_CONNECT_GRACE_SECONDS": 30,
    # Dotted path to `(request, *, caller, callee_id, thread_key) -> bool` —
    # WHO MAY RING WHOM. The default requires both parties to be participants
    # of the thread the call hangs off, read through
    # CALL_PARTICIPANTS_FUNCTION. Fail-closed: no thread, no answer, or no
    # verdict is a refusal. A user id is not a phone number, and without this
    # the id in a public listing page is a ringer.
    "CALL_AUTHORIZER": "stapel_video.calls.authorize.thread_participants",
    # Comm Function that answers "who is in this conversation". A NAME, not
    # an import: the answer comes over the bus from whichever service owns
    # conversations. "" disables the default authorizer, which then refuses
    # every call rather than allowing them.
    "CALL_PARTICIPANTS_FUNCTION": "chat.conversation_participants",
    # Comm Function that writes the call's system line into its thread
    # ("video.call.ended:188"). "" turns the thread line off; a deployment
    # with no chat says so here instead of collecting an exception per call.
    "CALL_THREAD_MESSAGE_FUNCTION": "chat.post_system_message",
    # Whether a ring also asks stapel-notifications for a push. AXIS. On by
    # default because the socket only reaches a browser that is open, and the
    # case a call feature exists for is a phone in a pocket. NOT gated on the
    # callee being offline — nothing in the fleet can answer that (see
    # calls/notify.py).
    "CALL_NOTIFY_ON_RING": True,
    # ── LiveKit default-provider credentials (tuning knobs, not axes) ──
    "LIVEKIT_URL": "",
    # Where the BROWSER connects — which is not where we connect. On a
    # host-networked deployment LIVEKIT_URL is http://host.docker.internal:7880,
    # an address no browser can resolve, and until 0.11.0 this module never
    # told a client where to dial so the collision never surfaced. Empty falls
    # back to LIVEKIT_URL, which is right for a deployment where they are the
    # same address and wrong silently for one where they are not — hence
    # stapel_video.W007.
    "LIVEKIT_CLIENT_URL": "",
    "LIVEKIT_API_KEY": "",
    "LIVEKIT_API_SECRET": "",
    # Join-token time-to-live in seconds.
    "JOIN_TOKEN_TTL_SECONDS": 3600,
    # Recording-egress object store (used only by start_room_egress). Left
    # empty: the recording pipeline is a seam in v0.1.0, not wired by default.
    "EGRESS_S3_ENDPOINT": "",
    "EGRESS_S3_BUCKET": "",
    "EGRESS_S3_ACCESS_KEY": "",
    "EGRESS_S3_SECRET_KEY": "",
    # ── Provider-webhook dispatch (MERGE registry) ────────────────────
    # {event type: dotted path to handler(parsed: dict) -> None | None to
    # remove a builtin}, merged OVER webhooks.BUILTIN_WEBHOOK_HANDLERS.
    # NOT an import_string: entries are resolved per key by webhooks.py, so
    # one broken path does not take the other handlers down with it, and
    # None can tombstone a builtin. Adding `room_finished` is a settings
    # line, not a fork of the ingress view.
    "WEBHOOK_HANDLERS": {},
    # ── Presence metering (spans) ─────────────────────────────────────
    # How often the reconciler compares the open spans against the live
    # roster the media server reports (seconds). This is the WORST-CASE
    # over-count of a lost `participant_left`: a zombie span is closed at
    # the last moment the sweeper confirmed the connection, so the error is
    # bounded by one interval rather than by however long until somebody
    # looked. Lower costs one ListParticipants per room with open spans.
    "PRESENCE_SWEEP_INTERVAL_SECONDS": 60,
    # Days a ParticipantSpan is kept before `purge_participant_spans`
    # deletes it. 400 = a full year of reporting plus a quarter of slack for
    # a late reconciliation or an audit. None = keep spans forever, which is
    # a decision a host states rather than drifts into.
    "PRESENCE_SPAN_RETENTION_DAYS": 400,
    # Cadence of that purge (crontab kwargs) — configuration, not a literal.
    "PRESENCE_PURGE_SCHEDULE": {"hour": 4, "minute": 10},
    # ── The per-scope usage read (0.7.0) ──────────────────────────────
    # The capability a caller must hold IN THE SCOPE THEY ASK ABOUT to read
    # its usage, resolved through the workspaces access registry
    # (`workspaces.check_capability`). Not `IsStaff`: the audience is a
    # workspace's own owner/admin looking at their own numbers, and a staff
    # gate would mean the only people who can see a customer's usage are the
    # vendor's employees. The host registers this capability on the roles it
    # means to grant it to, next to its own `members.invite`.
    "USAGE_MANDATE": "video.usage.read",
    # Dotted path to `(request, scope_key) -> bool`, consulted INSTEAD of the
    # registry in a deployment that cannot ask about mandates at all (no
    # stapel_workspaces, no routed seam). The default is staff-only, because
    # the honest fallback for "nothing here knows who owns this partition" is
    # the operator, not everyone.
    "USAGE_AUTHORIZER": "stapel_video.usage.staff_only_authorizer",
    # DRF rate for the `video-scope-usage` throttle scope, read from THIS
    # namespace: a library does not own the project's DEFAULT_THROTTLE_RATES.
    # The read is a full scan of one partition's months, so it is cheap to
    # ask for and not cheap to answer. None disables throttling.
    "USAGE_THROTTLE": "60/min",
}

video_settings = AppSettings(
    "STAPEL_VIDEO",
    defaults=DEFAULTS,
    import_strings=(
        "VIDEO_PROVIDER",
        "SCOPE_PROVIDER",
        "LIVE_ROOMS_PROVIDER",
        "USAGE_AUTHORIZER",
        "CALL_AUTHORIZER",
    ),
    # Keys that must NOT fall back to an environment variable. AppSettings
    # reads os.environ for every key not listed here and hands back the raw
    # STRING, which for a registry or a schedule is not the type the reader
    # expects — `WEBHOOK_HANDLERS="..."` would be iterated as characters.
    no_env=("WEBHOOK_HANDLERS", "PRESENCE_PURGE_SCHEDULE"),
)

__all__ = ["video_settings", "DEFAULTS"]
