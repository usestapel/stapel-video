"""First contact with a callee this service has never met (Д320 class).

The fleet measured it: the first call to a seconds-old account answered
``400 invalid_callee``, and a retry a few seconds later succeeded. Nothing
was wrong with the account. This service keeps a shadow ``users`` table whose
rows arrive either with a token (the caller's) or with the owner's
``user.created`` projection (everybody else's), and the projection is
asynchronous — so the callee's row simply had not landed yet, and a race was
answered as a bad request.

A local miss is therefore a question, not a verdict.
"""
import uuid
from contextlib import contextmanager

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db


LOOKUP = "video.test_user_lookup"
THREAD = "conv-1"


@pytest.fixture(autouse=True)
def _open_gate(settings):
    # A shadow store, which is what a downstream service of the fleet is:
    # its users table is a copy of the issuer's. `get_or_create_user_from_jwt`
    # refuses to write in authoritative mode, and so, correctly, does the
    # mirror — see the last test.
    settings.JWT_CREATE_USERS_FROM_TOKEN = True
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_AUTHORIZER": "stapel_video.calls.authorize.allow_any",
        "CALL_THREAD_MESSAGE_FUNCTION": "",
        "CALL_NOTIFY_ON_RING": False,
        "CALL_USER_LOOKUP_FUNCTION": LOOKUP,
    }


@pytest.fixture
def issuer():
    """A user the ISSUER knows and this service has never heard of.

    Registered as the comm Function the module asks by name, so the test
    exercises the seam the deployment uses rather than a patched internal.
    """
    known: dict[str, dict] = {}
    asked: list[str] = []

    def _lookup(payload):
        asked.append(str(payload.get("user_id")))
        return known.get(str(payload.get("user_id")))

    with _provider(LOOKUP, _lookup):
        yield type("Issuer", (), {"known": known, "asked": asked})


@contextmanager
def _provider(name, handler):
    """Register a comm Function for the duration of one test.

    The registry is process-wide and refuses a second provider for a name, so
    it is unregistered again — a leak here would make the next test's answer
    depend on which test ran first.
    """
    from stapel_core.comm import register_function
    from stapel_core.comm.registry import function_registry

    register_function(name, handler)
    try:
        yield
    finally:
        function_registry._providers.pop(name, None)
        function_registry._schemas.pop(name, None)


def _place(api_client, caller, callee_id):
    api_client.force_authenticate(user=caller)
    return api_client.post(
        reverse("video-calls"),
        {"callee_id": str(callee_id), "thread_key": THREAD},
        format="json",
    )


def test_a_callee_the_issuer_knows_is_mirrored_and_rung(api_client, user, issuer):
    """The defect, straight: a real account with no local row yet."""
    callee_id = uuid.uuid4()
    issuer.known[str(callee_id)] = {
        "user_id": str(callee_id),
        "username": "newcomer",
        "email": "newcomer@example.com",
        "is_active": True,
        "is_staff": False,
        "is_superuser": False,
    }
    assert not get_user_model().objects.filter(pk=callee_id).exists()

    resp = _place(api_client, user, callee_id)

    assert resp.status_code == 201, resp.data
    assert resp.data["call"]["callee_id"] == str(callee_id)
    # Mirrored, not invented: the row now exists and carries what the issuer
    # said, so the call has a foreign key and the next request pays nothing.
    mirrored = get_user_model().objects.get(pk=callee_id)
    assert mirrored.username == "newcomer"
    assert issuer.asked == [str(callee_id)]


def test_an_id_the_issuer_does_not_know_is_still_refused(api_client, user, issuer):
    """The lookup widens who can be rung by exactly nobody."""
    resp = _place(api_client, user, uuid.uuid4())
    assert resp.status_code == 400
    assert resp.data["localizable_error"] == "error.400.video_call_invalid_callee"


def test_a_local_callee_is_not_looked_up(api_client, user, other_user, issuer):
    """The round trip is paid on a miss, never on the ordinary call."""
    resp = _place(api_client, user, other_user.pk)
    assert resp.status_code == 201, resp.data
    assert issuer.asked == []


def test_a_lookup_that_cannot_be_reached_refuses(api_client, user, settings):
    """No Function registered under that name: the pre-0.11.2 answer, and a
    refusal rather than a call to somebody nothing vouched for."""
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "CALL_USER_LOOKUP_FUNCTION": "video.no_such_function",
    }
    resp = _place(api_client, user, uuid.uuid4())
    assert resp.status_code == 400


def test_a_deployment_that_asks_nobody_refuses(api_client, user, issuer, settings):
    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "CALL_USER_LOOKUP_FUNCTION": ""}
    callee_id = uuid.uuid4()
    issuer.known[str(callee_id)] = {"user_id": str(callee_id), "username": "n"}
    resp = _place(api_client, user, callee_id)
    assert resp.status_code == 400
    assert issuer.asked == []


def test_a_lookup_that_raises_refuses(api_client, user):
    """Fail-closed, like the authorizer: a lookup that reached no verdict is
    never an allowance."""

    def _boom(payload):
        raise RuntimeError("the issuer is down")

    with _provider(LOOKUP, _boom):
        resp = _place(api_client, user, uuid.uuid4())
    assert resp.status_code == 400


def test_an_authoritative_user_store_does_not_mirror(api_client, user, issuer, settings):
    """A service that OWNS its users table does not grow rows from a dial.

    ``JWT_CREATE_USERS_FROM_TOKEN=False`` is how a deployment says its
    database decides who exists. The mirror writes through the same function
    the JWT middleware does, so it inherits that answer rather than holding a
    second opinion about it.
    """
    settings.JWT_CREATE_USERS_FROM_TOKEN = False
    callee_id = uuid.uuid4()
    issuer.known[str(callee_id)] = {"user_id": str(callee_id), "username": "newcomer"}
    resp = _place(api_client, user, callee_id)
    assert resp.status_code == 400
    assert not get_user_model().objects.filter(pk=callee_id).exists()


def test_a_malformed_id_costs_no_round_trip(api_client, user, issuer):
    """An id the local table cannot even express is no issuer's user."""
    resp = _place(api_client, user, "not-a-uuid")
    assert resp.status_code == 400
    assert issuer.asked == []
