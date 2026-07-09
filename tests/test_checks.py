"""System checks: provider/scope misconfiguration fails loudly (§3.7)."""
from django.test import override_settings

from stapel_video import checks

FAKE = "stapel_video.tests.fakeprovider.FakeProvider"


@override_settings(STAPEL_VIDEO={"VIDEO_PROVIDER": FAKE})
def test_valid_config_passes():
    assert checks.check_video_provider(None) == []
    assert checks.check_scope_provider(None) == []
    assert checks.check_default_access_level(None) == []


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
