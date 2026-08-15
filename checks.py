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
    return check_shipped_scope_provider(
        setting="STAPEL_VIDEO['SCOPE_PROVIDER']",
        provider=provider,
        shipped_cls=DefaultScopeProvider,
        error_id="stapel_video.E009",
        warning_id="stapel_video.W002",
        isolates="room",
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

    Walked, not reversed: a host is free to mount the include under any prefix
    and any namespace, and a reverse() by name would read that as "not
    mounted". An unloadable URLconf answers True — Django's own url checks
    report that, and this check must not turn one defect into two.
    """
    try:
        from django.urls import get_resolver

        return _tree_has_video(get_resolver().url_patterns)
    except Exception:
        return True


def _tree_has_video(patterns) -> bool:
    for pattern in patterns:
        nested = getattr(pattern, "url_patterns", None)
        if nested is not None:
            if _tree_has_video(nested):
                return True
            continue
        callback = getattr(pattern, "callback", None)
        view_class = getattr(callback, "view_class", None) or getattr(
            callback, "cls", None
        )
        module = getattr(view_class or callback, "__module__", "") or ""
        if module == "stapel_video" or module.startswith("stapel_video."):
            return True
    return False


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
