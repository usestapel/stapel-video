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
- The presence sweeper / span retention not scheduled in a deployment that
  drives a beat schedule -> W. Both are jobs whose absence is invisible:
  unswept spans stay open and keep counting, unpurged spans just accumulate.
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


def _scheduled(schedule, task_name: str) -> bool:
    return any(
        (entry or {}).get("task") == task_name
        for entry in schedule.values()
        if isinstance(entry, dict)
    )
