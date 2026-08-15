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
