"""``LIVEKIT_CLIENT_URL`` is a policy read against the request, not a constant.

Measured on a two-brand fleet: one image answers ``primary.example`` and
``secondary.example``, and every browser was handed ``wss://primary.example/rtc`` — the
secondary brand's page opening a socket across the boundary its cookies, its
CSP and its TLS name were scoped to. The three forms below are the fix, and
the fourth test is the case the fix must not make worse: a request with no
host to read.
"""
import pytest
from django.test import RequestFactory
from django.urls import reverse

from stapel_video.client_url import resolve_client_url

pytestmark = pytest.mark.django_db


PRIMARY = "wss://primary.example/rtc"
SECONDARY = "wss://secondary.example/rtc"


def _request(host="primary.example", *, secure=True):
    return RequestFactory().get("/", secure=secure, HTTP_HOST=host)


def _hostless():
    """A request that cannot name a host — Django's own ``get_host`` raises.

    Not a contrived object: a request that reaches a process with neither a
    ``Host:`` header nor a ``SERVER_NAME`` is what a badly configured proxy
    or an internal health probe produces.
    """
    request = RequestFactory().get("/")
    request.META.pop("HTTP_HOST", None)
    request.META.pop("SERVER_NAME", None)
    return request


# ── (a) an absolute URL ────────────────────────────────────────────────────


def test_an_absolute_url_is_every_host_s_answer():
    """What every deployment had before, and still right for one brand."""
    assert resolve_client_url(PRIMARY, _request("primary.example")) == PRIMARY
    assert resolve_client_url(PRIMARY, _request("secondary.example")) == PRIMARY
    assert resolve_client_url(PRIMARY, None) == PRIMARY


# ── (b) a path: the request's own host ─────────────────────────────────────


def test_a_path_means_the_requesting_host():
    assert resolve_client_url("/rtc", _request("primary.example")) == PRIMARY
    assert resolve_client_url("/rtc", _request("secondary.example")) == SECONDARY


def test_a_path_takes_its_scheme_from_the_request():
    """``ws`` for a plain request — the local stand, not a downgrade in prod."""
    assert resolve_client_url("/rtc", _request("localhost:8000", secure=False)) == (
        "ws://localhost/rtc"
    )


def test_a_path_with_no_request_answers_nothing():
    """There is no host to build one from, and inventing one is the defect."""
    assert resolve_client_url("/rtc", None) == ""


# ── (c) a mapping ──────────────────────────────────────────────────────────


MAP = {"primary.example": PRIMARY, "secondary.example": SECONDARY, "default": "wss://fallback/rtc"}


def test_a_mapping_answers_per_host():
    assert resolve_client_url(MAP, _request("primary.example")) == PRIMARY
    assert resolve_client_url(MAP, _request("secondary.example")) == SECONDARY


def test_a_mapping_ignores_the_port():
    """``:8443`` is not a brand."""
    assert resolve_client_url(MAP, _request("secondary.example:8443")) == SECONDARY


def test_an_unlisted_host_falls_back_to_the_default():
    assert resolve_client_url(MAP, _request("new-brand.example")) == "wss://fallback/rtc"


def test_a_mapping_value_may_itself_be_a_path():
    mapping = {"primary.example": PRIMARY, "default": "/rtc"}
    assert resolve_client_url(mapping, _request("secondary.example")) == SECONDARY


def test_an_unlisted_host_with_no_default_is_told_nothing():
    """Silence, never another brand's address — that is the whole defect."""
    assert resolve_client_url({"primary.example": PRIMARY}, _request("secondary.example")) == ""


# ── a request with no host ─────────────────────────────────────────────────


def test_a_request_without_a_host_header_falls_back_to_the_default():
    assert resolve_client_url(MAP, _hostless()) == "wss://fallback/rtc"


def test_a_request_without_a_host_header_gets_nothing_from_a_path():
    assert resolve_client_url("/rtc", _hostless()) == ""


# ── end to end: the URL the call endpoints actually answer ─────────────────


@pytest.fixture(autouse=True)
def _livekit_provider_open_gate(settings):
    settings.ALLOWED_HOSTS = ["*"]
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "VIDEO_PROVIDER": "stapel_video.providers.livekit.LiveKitProvider",
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.allow_any",
        "CALL_THREAD_MESSAGE_FUNCTION": "",
        "CALL_NOTIFY_ON_RING": False,
        "LIVEKIT_URL": "http://host.docker.internal:7880",
        "LIVEKIT_CLIENT_URL": {"primary.example": PRIMARY, "default": "/rtc"},
    }


def test_the_ring_answers_the_url_for_the_brand_that_asked(
    api_client, user, other_user, monkeypatch
):
    """Two brands, one image, one POST each — and two different sockets."""
    monkeypatch.setattr(
        "stapel_video.providers.livekit.LiveKitProvider.mint_call_token",
        lambda *a, **kw: "tok",
    )
    monkeypatch.setattr(
        "stapel_video.providers.livekit.LiveKitProvider.ensure_call_room",
        lambda *a, **kw: "room",
    )
    api_client.force_authenticate(user=user)
    body = {"callee_id": str(other_user.pk), "thread_key": "conv-1"}

    primary = api_client.post(
        reverse("video-calls"), body, format="json", HTTP_HOST="primary.example", secure=True
    )
    assert primary.status_code == 201, primary.data
    assert primary.data["url"] == PRIMARY

    call_id = primary.data["call"]["id"]
    remint = api_client.post(
        reverse("video-call-token", args=[call_id]),
        {},
        format="json",
        HTTP_HOST="secondary.example",
        secure=True,
    )
    assert remint.status_code == 200, remint.data
    # The same live call, re-minted from the other brand's page: the default
    # is a path, so the answer follows the host that asked rather than the
    # one the call was placed from.
    assert remint.data["url"] == SECONDARY
