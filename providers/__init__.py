"""Video provider seam — the pluggable video backend.

``VideoProvider`` is the ABC (base.py); ``LiveKitProvider`` (livekit.py) is the
default implementation behind the ``[livekit]`` extra. Resolve the configured
provider with :func:`get_video_provider`.
"""
from .base import (
    METADATA_SCOPE_KEY,
    VideoProvider,
    VideoProviderError,
    split_identity,
)


def get_video_provider() -> VideoProvider:
    """Resolve the configured provider (already import_string'd by conf).

    ``VIDEO_PROVIDER`` may be a class (instantiated once per call) or a
    ready-made instance.
    """
    from ..conf import video_settings

    provider = video_settings.VIDEO_PROVIDER
    return provider() if isinstance(provider, type) else provider


__all__ = [
    "METADATA_SCOPE_KEY",
    "VideoProvider",
    "VideoProviderError",
    "get_video_provider",
    "split_identity",
]
