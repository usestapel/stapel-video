"""stapel-video capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli

#: The three CTO-facing config axes (capability-config.md §16). Every OTHER
#: DEFAULTS key is an extension seam (SCOPE_PROVIDER dotted path) or a tuning
#: knob (LiveKit credentials, token TTL, egress store).
_AXES = {"VIDEO_PROVIDER", "DEFAULT_ACCESS_LEVEL", "DEFAULT_ADMIT_REQUIRED"}


def main(argv=None):
    from stapel_video._codegen import _configure

    _configure()
    from stapel_video.conf import DEFAULTS
    from stapel_video.urls import GATE_REGISTRY

    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/video",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in _AXES,
        axis_group=axis_group_rules(
            exact={
                "VIDEO_PROVIDER": "video.provider",
                "DEFAULT_ACCESS_LEVEL": "video.access",
                "DEFAULT_ADMIT_REQUIRED": "video.admission",
            }
        ),
        prog="stapel-video-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
