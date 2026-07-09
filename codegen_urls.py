"""Canonical-prefix URLconf for contract emission (contract-pipeline.md §2).

stapel-video's own ``urls.py`` already bakes ``api/`` into every path
(``api/rooms``, ``api/webhook``, ...) and documents its expected host mount::

    path("video/", include("stapel_video.urls"))

That is the canonical public API prefix (``video/api/...``) — the same
``<mod>/api/`` shape every other pair-backend uses. This harness urlconf
reproduces exactly that documented mount, so drf-spectacular emits
``/video/api/...`` paths. video is validated standalone (no monolith slice
exists yet to diff against; contract-pipeline.md §9 fallback path applies).
"""
from django.urls import include, path

urlpatterns = [
    path("video/", include("stapel_video.urls")),
]
