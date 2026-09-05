"""Django system checks for stapel-video configuration.

Policy (docs/library-standard.md §3.7): E-level for configuration the service
cannot run with; W-level for entries that only degrade lazily.

- VIDEO_PROVIDER unimportable / not a VideoProvider -> E (no room can mint a
  join token — the module cannot serve calls).
- SCOPE_PROVIDER unimportable / not a ScopeProvider -> E (create & the
  scope_trusted join decision cannot resolve scope/membership).
- DEFAULT_ACCESS_LEVEL not a valid access level -> E (every default-level room
  create would produce an unjoinable room).
- LIVE_ROOMS_PROVIDER unimportable / not a LiveRoomsProvider -> E (the
  profile.changed subscriber cannot find anybody's live calls).
- LIVE_ROOMS_PROVIDER left at its default in a deployment that does not mount
  this module's URLs -> E. See below: this one is a wiring invariant, not a
  type check, and it is the reason the seam is allowed to have a default at
  all.
- A WEBHOOK_HANDLERS overlay entry that cannot be imported or is not callable
  -> E (the event it claims to handle is silently unhandled otherwise, and a
  webhook that does nothing looks exactly like a webhook that was never sent).
- USAGE_AUTHORIZER unimportable / not callable -> E (the per-scope usage read
  would 500 behind its own authorization gate, in exactly the deployment
  shape — no workspaces — where that fallback is the only authority).
- The presence sweeper / span retention not scheduled in a deployment that
  drives a beat schedule -> W. Both are jobs whose absence is invisible:
  unswept spans stay open and keep counting, unpurged spans just accumulate.
- The realtime substrate installed with no signal transport configured -> W.
  The lobby socket then accepts clients and delivers nothing, which looks
  exactly like a working socket from every side but the guest's.
"""
from django.core import checks
from stapel_core.django.scope import check_shipped_scope_provider


@checks.register(checks.Tags.compatibility)
def check_video_provider(app_configs, **kwargs):
    from .conf import video_settings
    from .providers import VideoProvider

    try:
        provider = video_settings.VIDEO_PROVIDER
    except Exception as exc:
        return [
            checks.Error(
                f"STAPEL_VIDEO['VIDEO_PROVIDER'] could not be imported: {exc}",
                id="stapel_video.E001",
            )
        ]
    target = provider if isinstance(provider, type) else type(provider)
    if not issubclass(target, VideoProvider):
        return [
            checks.Error(
                "STAPEL_VIDEO['VIDEO_PROVIDER'] must be a VideoProvider subclass",
                id="stapel_video.E002",
            )
        ]
    return []


@checks.register(checks.Tags.compatibility)
def check_scope_provider(app_configs, **kwargs):
    from .conf import video_settings
    from .scope import DefaultScopeProvider, ScopeProvider

    try:
        provider = video_settings.SCOPE_PROVIDER
    except Exception as exc:
        return [
            checks.Error(
                f"STAPEL_VIDEO['SCOPE_PROVIDER'] could not be imported: {exc}",
                id="stapel_video.E003",
            )
        ]
    target = provider if isinstance(provider, type) else type(provider)
    if not issubclass(target, ScopeProvider):
        return [
            checks.Error(
                "STAPEL_VIDEO['SCOPE_PROVIDER'] must be a ScopeProvider subclass",
                id="stapel_video.E004",
            )
        ]
    # Importable and correctly typed says nothing about whether the shipped
    # single-scope provider is still deciding who is a trusted scope member.
    # MEASURED, not assumed — the same reading E008 makes below. A host that
    # installs this module for its provider seam and its subscribers, and
    # leaves the rooms surface unmounted, owns its own rooms: the shipped
    # provider decides nothing there because nothing routes to the code that
    # would consult it. Refusing that boot would demand a provider that
    # provably never runs (meettoday, 2026-08-16).
    return check_shipped_scope_provider(
        setting="STAPEL_VIDEO['SCOPE_PROVIDER']",
        provider=provider,
        shipped_cls=DefaultScopeProvider,
        error_id="stapel_video.E009",
        warning_id="stapel_video.W002",
        isolates="room",
        surface_mounted=_video_urls_mounted(),
    )


@checks.register(checks.Tags.compatibility)
def check_live_rooms_provider(app_configs, **kwargs):
    from .conf import video_settings
    from .live_rooms import LiveRoomsProvider

    try:
        provider = video_settings.LIVE_ROOMS_PROVIDER
    except Exception as exc:
        return [
            checks.Error(
                f"STAPEL_VIDEO['LIVE_ROOMS_PROVIDER'] could not be imported: {exc}",
                id="stapel_video.E006",
            )
        ]
    target = provider if isinstance(provider, type) else type(provider)
    if not issubclass(target, LiveRoomsProvider):
        return [
            checks.Error(
                "STAPEL_VIDEO['LIVE_ROOMS_PROVIDER'] must be a LiveRoomsProvider "
                "subclass",
                id="stapel_video.E007",
            )
        ]
    return []


@checks.register(checks.Tags.compatibility)
def check_live_rooms_source_is_writable(app_configs, **kwargs):
    """The seam's own failure mode, closed at boot instead of at a stale tile.

    ``LIVE_ROOMS_PROVIDER`` has a default because most hosts use this module's
    rooms. A host that installs ``stapel_video`` for the provider seam alone,
    keeps its own Room model and never touches this default gets a subscriber
    that answers "no live rooms" for everybody, forever — and empty-because-
    unconfigured is indistinguishable from empty-because-nobody-is-on-a-call,
    so nothing anywhere reports it. That is the fleet's root defect class
    verbatim: a seam whose misconfiguration is silent.

    It is statically detectable, so it is closed as a MEASUREMENT rather than
    a name check. The default's data source — this module's Room /
    RoomParticipant rows — is written by exactly one thing: this module's own
    join/admit endpoints. If they are not mounted, nothing in this deployment
    can ever populate what the default reads. That is a wiring invariant, and
    a host that adapted correctly (its own provider, or this module's URLs)
    passes it without knowing it exists.
    """
    from .conf import video_settings
    from .live_rooms import DefaultLiveRoomsProvider

    try:
        provider = video_settings.LIVE_ROOMS_PROVIDER
    except Exception:
        return []  # already reported by check_live_rooms_provider
    target = provider if isinstance(provider, type) else type(provider)
    if target is not DefaultLiveRoomsProvider:
        return []
    if _video_urls_mounted():
        return []
    return [
        checks.Error(
            "STAPEL_VIDEO['LIVE_ROOMS_PROVIDER'] is the default (it reads "
            "stapel_video's own Room/RoomParticipant tables), but this "
            "deployment does not mount stapel_video's URLs — so nothing here "
            "ever writes those tables and the 'profile.changed' subscriber "
            "will answer 'no live rooms' for every user, forever: a rename "
            "will silently never reach a call in progress. Either mount the "
            "module's URL surface (path('video/', "
            "include('stapel_video.urls'))) so its own join endpoint fills "
            "those tables, or point STAPEL_VIDEO['LIVE_ROOMS_PROVIDER'] at a "
            "LiveRoomsProvider over the rooms this app actually writes.",
            id="stapel_video.E008",
        )
    ]


def _video_urls_mounted() -> bool:
    """Is any stapel_video view reachable in this deployment's URLconf?

    The walk itself lives in stapel_core.django.mounts (core 0.30.0) — two
    checks here need it and the same question is E009's in every module that
    ships a scope seam, so the mechanism belongs one layer down rather than
    re-copied per library.
    """
    from stapel_core.django.mounts import module_urls_mounted

    return module_urls_mounted("stapel_video")


@checks.register(checks.Tags.compatibility)
def check_default_access_level(app_configs, **kwargs):
    from .conf import video_settings
    from .models import AccessLevel

    if video_settings.DEFAULT_ACCESS_LEVEL not in AccessLevel.values:
        return [
            checks.Error(
                "STAPEL_VIDEO['DEFAULT_ACCESS_LEVEL'] must be one of "
                f"{list(AccessLevel.values)}.",
                id="stapel_video.E005",
            )
        ]
    return []


@checks.register(checks.Tags.compatibility)
def check_webhook_handlers(app_configs, **kwargs):
    """E010: every WEBHOOK_HANDLERS overlay entry resolves to a callable.

    The dispatch registry skips a broken entry at runtime and logs it, which
    keeps a live webhook from 500-ing over somebody's typo — but a log line
    in a webhook handler is not where an operator finds out that the event
    they wired is doing nothing. Boot is.
    """
    from django.utils.module_loading import import_string

    from .conf import video_settings

    errors = []
    for event, target in (video_settings.WEBHOOK_HANDLERS or {}).items():
        if target is None or target == "":
            continue  # tombstoning a builtin is a legitimate entry
        if callable(target):
            continue
        try:
            handler = import_string(target)
        except Exception as exc:
            errors.append(
                checks.Error(
                    f"STAPEL_VIDEO['WEBHOOK_HANDLERS'][{event!r}] is "
                    f"configured but broken: {exc}",
                    hint=(
                        f"Point it at a callable taking the normalized event "
                        f"dict, or set it to None to stop handling {event!r}."
                    ),
                    id="stapel_video.E010",
                )
            )
            continue
        if not callable(handler):
            errors.append(
                checks.Error(
                    f"STAPEL_VIDEO['WEBHOOK_HANDLERS'][{event!r}] -> "
                    f"{target!r} is not callable.",
                    id="stapel_video.E010",
                )
            )
    return errors


@checks.register(checks.Tags.compatibility)
def check_usage_authorizer(app_configs, **kwargs):
    """E011: the usage read's fallback authorizer is broken or is not callable.

    It is only consulted in a deployment that cannot ask about mandates, which
    is exactly the deployment where nobody notices it is missing until the
    first admin opens the usage screen and gets a 500. Boot is a better place
    to find out than a stack trace behind an authorization gate.
    """
    from .conf import video_settings

    try:
        authorizer = video_settings.USAGE_AUTHORIZER
    except Exception as exc:
        return [
            checks.Error(
                f"STAPEL_VIDEO['USAGE_AUTHORIZER'] could not be imported: {exc}",
                hint=(
                    "Point it at a callable (request, scope_key) -> bool, or "
                    "drop the setting to use the staff-only default."
                ),
                id="stapel_video.E011",
            )
        ]
    if not callable(authorizer):
        return [
            checks.Error(
                "STAPEL_VIDEO['USAGE_AUTHORIZER'] must be a callable "
                "(request, scope_key) -> bool.",
                id="stapel_video.E011",
            )
        ]
    return []


@checks.register(checks.Tags.compatibility)
def check_presence_sweep_is_scheduled(app_configs, **kwargs):
    """W003: nothing in the beat schedule reconciles open presence spans.

    Every presence span this instance opens is closed by one of three things:
    a ``participant_left`` webhook, an explicit leave, or the sweeper. The
    first two are the happy paths and the third is the only one that covers a
    crashed client whose departure was never reported, a webhook the provider
    dropped, or a room the media server restarted under. Without it those
    spans stay open and keep accruing time for as long as the table lives —
    an over-count with no upper bound, in the numbers a licence is sold on.

    Only hosts that drive a beat schedule are checked: a host with no
    ``CELERY_BEAT_SCHEDULE`` runs ``manage.py video_sweep_presence`` from its
    own cron, which this process cannot see and must not second-guess.
    """
    from django.conf import settings

    from .tasks import SWEEP_TASK_NAME

    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None)
    if not schedule:
        return []
    if _scheduled(schedule, SWEEP_TASK_NAME):
        return []
    return [
        checks.Warning(
            "CELERY_BEAT_SCHEDULE has no entry for "
            f"{SWEEP_TASK_NAME}: a presence span whose provider webhook was "
            "lost will stay open forever and keep accruing billable time.",
            hint=(
                "CELERY_BEAT_SCHEDULE = {**get_video_beat_schedule(), ...} "
                "(stapel_video.tasks), or run the video_sweep_presence "
                "management command from cron on the "
                "PRESENCE_SWEEP_INTERVAL_SECONDS cadence."
            ),
            id="stapel_video.W003",
        )
    ]


@checks.register(checks.Tags.compatibility)
def check_presence_retention_is_scheduled(app_configs, **kwargs):
    """W004: the span retention window is set and nothing enforces it.

    ``PRESENCE_SPAN_RETENTION_DAYS = None`` is not reported: keeping the
    spans forever is then a stated decision, not an accident.
    """
    from django.conf import settings

    from .conf import video_settings
    from .tasks import PURGE_TASK_NAME

    if video_settings.PRESENCE_SPAN_RETENTION_DAYS is None:
        return []
    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None)
    if not schedule:
        return []
    if _scheduled(schedule, PURGE_TASK_NAME):
        return []
    return [
        checks.Warning(
            "STAPEL_VIDEO['PRESENCE_SPAN_RETENTION_DAYS'] is "
            f"{video_settings.PRESENCE_SPAN_RETENTION_DAYS!r}, but "
            f"CELERY_BEAT_SCHEDULE has no entry for {PURGE_TASK_NAME}: "
            "presence spans are kept for as long as the table exists, "
            "whatever the setting says.",
            hint=(
                "CELERY_BEAT_SCHEDULE = {**get_video_beat_schedule(), ...} "
                "(stapel_video.tasks), run the video_purge_spans management "
                "command from cron, or set PRESENCE_SPAN_RETENTION_DAYS = "
                "None to state that this deployment keeps spans indefinitely."
            ),
            id="stapel_video.W004",
        )
    ]


@checks.register(checks.Tags.compatibility)
def check_lobby_stream_is_deliverable(app_configs, **kwargs):
    """W005: a lobby socket that accepts clients and never says anything.

    The failure this exists for is the quiet one, and this module has already
    shipped it once: ``stapel_realtime`` in INSTALLED_APPS means the host
    meant to serve the lobby stream, so the consumer connects, authorizes and
    sits there — but the frames are emitted through
    ``stapel_core.comm.signal()``, which is a **silent no-op** until
    ``STAPEL_COMM["SIGNAL_TRANSPORT"]`` names a transport. Nothing errors and
    nothing logs: the socket is up, the lobby never moves, and the only
    symptom is a host clicking Admit while the guest's screen still says
    "waiting".

    Not having the substrate at all is not a defect and is not reported: the
    lobby is complete over REST, and an HTTP-only host's clients re-read it.
    The warning is for the half-configured middle.
    """
    from django.apps import apps

    if not apps.is_installed("stapel_realtime"):
        return []

    # The core's own resolution, not a re-reading of the setting: "none", an
    # empty value and an unresolvable dotted path all mean "signals are
    # dropped on this host", and only signal_transport() knows all three.
    from stapel_core.comm import signal_transport

    if signal_transport() is not None:
        return []

    return [
        checks.Warning(
            "stapel_realtime is installed, so the lobby socket "
            "(ws/video/lobby/<join_code>) will accept clients — but "
            "STAPEL_COMM['SIGNAL_TRANSPORT'] is unset, so signal() is a "
            "no-op and no lobby frame is ever delivered. Every guest sits on "
            "a silent socket and only learns the host's verdict by "
            "re-posting the join.",
            hint=(
                "Set STAPEL_COMM['SIGNAL_TRANSPORT'] = 'channels' (with "
                "CHANNEL_LAYERS configured), or drop stapel_realtime from "
                "INSTALLED_APPS and let clients read the lobby over REST "
                "deliberately."
            ),
            id="stapel_video.W005",
        )
    ]


def _scheduled(schedule, task_name: str) -> bool:
    return any(
        (entry or {}).get("task") == task_name
        for entry in schedule.values()
        if isinstance(entry, dict)
    )


@checks.register(checks.Tags.compatibility)
def check_call_sweep_is_scheduled(app_configs, **kwargs):
    """W006: nothing expires a ring, and nothing closes a call nobody ended.

    Two failures, one missing beat entry, and a person is waiting on both.

    A ringing call past its deadline already READS as missed
    (``calls.services._expire_if_overdue``), so this is not about the answer
    being wrong. It is about the transitions that only a reader triggers:
    with neither party looking — which is exactly the case where a call goes
    unanswered — nobody is sent the missed-call push and no line is written
    into the thread.

    The other half is worse, because it does not heal. An accepted call whose
    ``participant_left`` was lost stays accepted forever: metered, blocking
    both parties from calling anybody else, and showing an in-call panel that
    will not close. Webhook delivery is at-least-once, which is a promise
    about duplicates and not about losses.

    Only hosts that drive a beat schedule are checked: a host with no
    ``CELERY_BEAT_SCHEDULE`` runs the management command from its own cron,
    which this process cannot see and must not second-guess.
    """
    from django.conf import settings

    from .tasks import CALL_SWEEP_TASK_NAME

    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None)
    if not schedule:
        return []
    if _scheduled(schedule, CALL_SWEEP_TASK_NAME):
        return []
    return [
        checks.Warning(
            "CELERY_BEAT_SCHEDULE has no entry for "
            f"{CALL_SWEEP_TASK_NAME}: an unanswered call will never send its "
            "missed-call notification, and a call whose provider webhook was "
            "lost will stay open forever — metered, and blocking both parties "
            "from placing another call.",
            hint=(
                "CELERY_BEAT_SCHEDULE = {**get_video_beat_schedule(), ...} "
                "(stapel_video.tasks), or run the video_sweep_calls "
                "management command from cron on the "
                "CALL_SWEEP_INTERVAL_SECONDS cadence."
            ),
            id="stapel_video.W006",
        )
    ]


@checks.register(checks.Tags.compatibility)
def check_call_client_url_is_reachable_by_a_browser(app_configs, **kwargs):
    """W007: the address the browser is handed is the one only we can reach.

    ``LIVEKIT_URL`` is where THIS PROCESS posts twirp calls. On a
    host-networked deployment that is something like
    ``http://host.docker.internal:7880`` — an address no browser can resolve,
    and no ``wss://`` either. Until calls existed no endpoint of this module
    ever told a client where to connect, so the two were never distinguished
    and one name did both jobs.

    Now ``POST /calls`` answers a ``url`` the browser dials. If
    ``LIVEKIT_CLIENT_URL`` is unset the client is handed the server's own
    upstream, which is right when they genuinely are the same address and
    silently wrong when they are not: the token is valid, the room exists,
    the call simply never connects and nothing in any log says why.

    Reported only when the fallback would produce an address a browser cannot
    use — a ``host.docker.internal`` / ``localhost`` host, or a plain
    ``http://``. A deployment where both are ``wss://example.com/rtc`` is
    correct and is not nagged.
    """
    from .conf import video_settings

    if video_settings.LIVEKIT_CLIENT_URL:
        return []
    url = (video_settings.LIVEKIT_URL or "").strip()
    if not url:
        return []
    suspicious = (
        "host.docker.internal" in url
        or "://localhost" in url
        or "://127.0.0.1" in url
        or url.startswith("http://")
        or url.startswith("ws://")
    )
    if not suspicious:
        return []
    return [
        checks.Warning(
            "STAPEL_VIDEO['LIVEKIT_CLIENT_URL'] is unset, so POST /calls "
            f"hands browsers {url!r} — this process's own upstream address. "
            "If that is not reachable from a browser the call will mint a "
            "valid token, name a real room, and never connect.",
            hint=(
                "Set LIVEKIT_CLIENT_URL to the public signalling address "
                "(e.g. wss://example.com/rtc) and leave LIVEKIT_URL as the "
                "address this service reaches the media server on."
            ),
            id="stapel_video.W007",
        )
    ]
