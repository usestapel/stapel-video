"""Single-module Django settings for stapel-video's harnesses.

Single source of truth for the ``settings.configure(...)`` block shared by:

  - the pytest suite (``conftest.py``) — mounts video on its *bare* test
    urlconf (``stapel_video.tests.urls`` -> ``video/`` -> the module's own
    ``api/rooms`` etc.); and
  - the contract-emission harness (``_codegen.py`` / ``make contract``) —
    mounts video on its *canonical* public API prefix
    (``stapel_video.codegen_urls`` -> ``video/`` -> same ``api/*`` paths the
    module's own ``urls.py`` declares) and enables drf-spectacular, so the
    emitted ``schema.json`` / ``flows.json`` paths are byte-identical to what a
    host mounting this module would serve (contract-pipeline.md §2).

Keeping one copy here means the harness and the tests can never drift in their
``INSTALLED_APPS`` / mock config. Copied from stapel-calendar's etalon and
tailored to this module (no gdpr sibling; adds the in-process comm bus + schema
validation the suite needs).
"""
from __future__ import annotations


def settings_kwargs(
    *,
    root_urlconf: str = "stapel_video.tests.urls",
    contract: bool = False,
) -> dict:
    """Return the ``settings.configure(**kwargs)`` for a single-module video
    instance. ``contract=True`` swaps in the production ``REST_FRAMEWORK`` (the
    canonical stapel-core config, inlined) so the emitted schema carries the
    real permission/renderer/schema classes — kept in lockstep by
    test_contract.py's identity gate."""
    if contract:
        rest_framework = {
            "DEFAULT_AUTHENTICATION_CLASSES": [
                "stapel_core.django.jwt.authentication.JWTCookieAuthentication",
            ],
            "DEFAULT_PERMISSION_CLASSES": [
                "stapel_core.django.api.permissions.IsServiceRequest",
                "stapel_core.django.api.permissions.IsSuperUser",
            ],
            "DEFAULT_RENDERER_CLASSES": [
                "rest_framework.renderers.JSONRenderer",
                "rest_framework.renderers.BrowsableAPIRenderer",
            ],
            "DEFAULT_SCHEMA_CLASS": "stapel_core.django.openapi.schemas.PermissionAwareAutoSchema",
            "EXCEPTION_HANDLER": "stapel_core.django.api.errors.stapel_exception_handler",
        }
    else:
        rest_framework = None

    kwargs = dict(
        SECRET_KEY="test-secret-key-not-for-production",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.admin",
            "django.contrib.messages",
            # CommonDjangoConfig ships the stapel_core management commands
            # (generate_error_keys, used by the errors.json drift gate).
            "stapel_core.django.apps.CommonDjangoConfig",
            "stapel_core.django.users",
            "rest_framework",
            "drf_spectacular",
            "stapel_video",
        ],
        AUTH_USER_MODEL="users.User",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        USE_TZ=True,
        ROOT_URLCONF=root_urlconf,
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            }
        },
        # Synchronous in-process comm with schema validation ON, so the
        # committed contracts in schemas/ are enforced by the tests.
        STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
        STAPEL_COMM={
            "OUTBOX_ENABLED": False,
            "ACTION_TRANSPORT": "inprocess",
            "VALIDATE_SCHEMAS": True,
        },
        # In-memory channel layer for the realtime lobby consumer tests.
        CHANNEL_LAYERS={
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        },
        # Test video provider (no network) — overridden per-test where needed.
        STAPEL_VIDEO={
            "VIDEO_PROVIDER": "stapel_video.tests.fakeprovider.FakeProvider",
        },
        # Skip migrations — create tables directly from models.
        MIGRATION_MODULES={
            "users": None,
            "video": None,
        },
    )
    if rest_framework is not None:
        kwargs["REST_FRAMEWORK"] = rest_framework
    return kwargs


# The multi-module common path prefix drf-spectacular auto-detects in a
# multi-module aggregate. Forced on the drf-spectacular settings singleton by
# the harness so a single-module instance derives the same style of
# operationIds. Uniform across all pair-backends.
CODEGEN_SCHEMA_PATH_PREFIX = "/"
