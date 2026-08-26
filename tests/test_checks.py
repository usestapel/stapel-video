"""System checks: provider/scope misconfiguration fails loudly (§3.7)."""
from django.test import override_settings

from stapel_video import checks

FAKE = "stapel_video.tests.fakeprovider.FakeProvider"


SCOPED = "stapel_video.tests.fakescope.UsernameScopeProvider"


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE})
def test_valid_config_passes():
    assert checks.check_video_provider(None) == []
    assert checks.check_default_access_level(None) == []


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE})
def test_the_shipped_provider_warns_in_a_standalone_deployment():
    """No longer silent: importability and type said nothing about a provider
    that calls every account a trusted scope member."""
    assert [m.id for m in checks.check_scope_provider(None)] == ["stapel_video.W002"]


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE})
def test_the_shipped_provider_is_an_error_where_workspaces_can_answer():
    """The finding the old check could not make: this deployment knows what a
    mandate is, and the shipped provider cannot name a tenant."""
    from stapel_core.comm import function_registry
    from stapel_core.django.mandate import MANDATE_FUNCTION, MANDATE_RESULT_KEY

    function_registry.register(
        MANDATE_FUNCTION, lambda payload: {MANDATE_RESULT_KEY: True}
    )
    try:
        msgs = checks.check_scope_provider(None)
    finally:
        function_registry._providers.pop(MANDATE_FUNCTION, None)
    assert [m.id for m in msgs] == ["stapel_video.E009"]


@override_settings(
    STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE},
    ROOT_URLCONF="stapel_video.tests.urls_unmounted",
)
def test_an_unmounted_surface_downgrades_the_error_to_a_warning():
    """meettoday, 2026-08-16: E009 kept a sandbox down over a hole it lacked.

    A host that owns its own rooms installs this module for its provider seam
    and its subscribers and never mounts the views. Nothing routes to
    ``services.join_room``, so the shipped provider decides nothing — and the
    only way to satisfy an Error there is a provider that provably never runs.
    E008 was already gating on this same URLconf walk; E009 was not, and that
    asymmetry inside one file was the defect.
    """
    from stapel_core.comm import function_registry
    from stapel_core.django.mandate import MANDATE_FUNCTION, MANDATE_RESULT_KEY

    function_registry.register(
        MANDATE_FUNCTION, lambda payload: {MANDATE_RESULT_KEY: True}
    )
    try:
        msgs = checks.check_scope_provider(None)
    finally:
        function_registry._providers.pop(MANDATE_FUNCTION, None)
    assert [m.id for m in msgs] == ["stapel_video.W002"]
    assert "not mounted" in msgs[0].msg


@override_settings(
    STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE, "SCOPE_PROVIDER": SCOPED},
    ROOT_URLCONF="stapel_video.tests.urls_unmounted",
)
def test_an_unmounted_surface_still_looks_at_the_provider():
    """Unmounted is not a licence to stop reading SCOPE_PROVIDER at all."""
    assert checks.check_scope_provider(None) == []


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE, "SCOPE_PROVIDER": SCOPED})
def test_a_real_swap_is_silent():
    assert checks.check_scope_provider(None) == []


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": "stapel_video.nope.Missing"})
def test_unimportable_provider_is_error():
    errors = checks.check_video_provider(None)
    assert errors and errors[0].id == "stapel_video.E001"


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": "stapel_video.models.Room"})
def test_non_provider_is_error():
    errors = checks.check_video_provider(None)
    assert errors and errors[0].id == "stapel_video.E002"


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE, "DEFAULT_ACCESS_LEVEL": "bogus"})
def test_bad_default_access_level_is_error():
    errors = checks.check_default_access_level(None)
    assert errors and errors[0].id == "stapel_video.E005"


# ── W005: the half-configured lobby socket ──────────────────────────────
#
# The suite runs with SIGNAL_TRANSPORT = "channels" (that is the deployment
# this module is built for), so the silent-socket arm is reached by taking it
# away — which is exactly the deployment that shipped in 0.8.0.


def test_a_served_lobby_stream_with_no_transport_is_a_warning():
    with override_settings(STAPEL_COMM={"SIGNAL_TRANSPORT": "none"}):
        msgs = checks.check_lobby_stream_is_deliverable(None)
    assert [m.id for m in msgs] == ["stapel_video.W005"]


def test_a_configured_transport_is_silent():
    assert checks.check_lobby_stream_is_deliverable(None) == []


def test_a_host_without_the_substrate_is_not_scolded():
    """No socket served is not a defect: the lobby is complete over REST."""
    from unittest.mock import patch

    with patch("django.apps.apps.is_installed", return_value=False):
        with override_settings(STAPEL_COMM={"SIGNAL_TRANSPORT": "none"}):
            assert checks.check_lobby_stream_is_deliverable(None) == []
