"""Django system checks for stapel-video configuration.

Policy (docs/library-standard.md §3.7): E-level for configuration the service
cannot run with; W-level for entries that only degrade lazily.

- VIDEO_PROVIDER unimportable / not a VideoProvider -> E (no room can mint a
  join token — the module cannot serve calls).
- SCOPE_PROVIDER unimportable / not a ScopeProvider -> E (create & the
  scope_trusted join decision cannot resolve scope/membership).
- DEFAULT_ACCESS_LEVEL not a valid access level -> E (every default-level room
  create would produce an unjoinable room).
"""
from django.core import checks


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
    from .scope import ScopeProvider

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
    return []


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
