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

Two more **config axes** (capability-config.md §16) set room defaults at
creation time when the client does not specify them:

- ``DEFAULT_ACCESS_LEVEL`` — public | scope_trusted | restricted.
- ``DEFAULT_ADMIT_REQUIRED`` — whether a fresh room starts with the lobby on.

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
    # Default access level for a room created without an explicit one. Axis
    # (capability-config.md §16): public (anyone with the code joins
    # instantly), scope_trusted (scope members auto-admit, others wait),
    # restricted (everyone but the host waits in the lobby).
    "DEFAULT_ACCESS_LEVEL": "restricted",
    # Default lobby switch for a room created without an explicit one. Axis:
    # True means non-auto-admitted joiners wait for a host to let them in.
    "DEFAULT_ADMIT_REQUIRED": True,
    # ── LiveKit default-provider credentials (tuning knobs, not axes) ──
    "LIVEKIT_URL": "",
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
}

video_settings = AppSettings(
    "STAPEL_VIDEO",
    defaults=DEFAULTS,
    import_strings=("VIDEO_PROVIDER", "SCOPE_PROVIDER"),
)

__all__ = ["video_settings", "DEFAULTS"]
