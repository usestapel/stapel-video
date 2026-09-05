"""stapel-video capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli

#: The CTO-facing config axes (capability-config.md §16). Every OTHER DEFAULTS
#: key is an extension seam (SCOPE_PROVIDER, CALL_AUTHORIZER — dotted paths) or
#: a tuning knob (LiveKit credentials, sweep cadences, egress store).
#:
#: The four call keys added in 0.11.0 are axes and not knobs because each one
#: is visible to the two people on the call: how long their phone rings, how
#: long a call may run, whether it reaches them out of the app at all, and who
#: is allowed to reach them. CALL_AUTHORIZER is both — an axis (a product
#: decision about reachability) and a seam (a dotted path), and it is listed in
#: both sections for that reason rather than being filed under whichever one
#: was noticed first.
_AXES = {
    "VIDEO_PROVIDER",
    "DEFAULT_ACCESS_LEVEL",
    "DEFAULT_ADMIT_REQUIRED",
    "CALL_RING_TIMEOUT_SECONDS",
    "CALL_MAX_DURATION_SECONDS",
    "CALL_NOTIFY_ON_RING",
    "CALL_AUTHORIZER",
}


def main(argv=None):
    from stapel_video._codegen import _configure

    _configure()
    from stapel_video.conf import DEFAULTS
    from stapel_video.urls import GATE_REGISTRY

    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/video/api/v1",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in _AXES,
        axis_group=axis_group_rules(
            exact={
                "VIDEO_PROVIDER": "video.provider",
                "DEFAULT_ACCESS_LEVEL": "video.access",
                "DEFAULT_ADMIT_REQUIRED": "video.admission",
                "CALL_RING_TIMEOUT_SECONDS": "video.calls",
                "CALL_MAX_DURATION_SECONDS": "video.calls",
                "CALL_NOTIFY_ON_RING": "video.calls",
                "CALL_AUTHORIZER": "video.calls",
            }
        ),
        prog="stapel-video-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
