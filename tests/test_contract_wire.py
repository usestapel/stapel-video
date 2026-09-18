"""Every response body the contract declares is a body the views actually send.

``docs/schema.json`` is emitted from the views' ``@extend_schema``
annotations, and an annotation is a CLAIM: it says what the view returns, and
the generator has no way to check it against the method body.
``tests/test_contract.py`` compares the committed document against a FRESH
EMISSION of the same annotations — it proves the file is not stale, and
nothing else, because both sides come from the claim. stapel-alerts 0.2.0
shipped ``GET /issues`` declared as ``Issue[]`` while the wire carried
``{count, offset, limit, results}``: the drift gate was green and the
frontend pair rendered ``undefined``.

This is the gate the generator cannot be: it performs every operation the
committed schema declares with a JSON response body, and validates the body
it gets against the schema it was promised.

Rules this file holds itself to:

* an operation with a declared JSON response and no entry in ``RECIPES``
  FAILS LOUDLY — a gate that quietly covers thirteen of fourteen rows is the
  family of green that proves nothing;
* a path parameter the gate cannot fill fails at the point of substitution,
  naming the operation;
* the operations that genuinely cannot be driven in-process are listed by
  name in ``UNDRIVABLE`` with a one-line reason each. That list is asserted
  to be exactly current: a stale entry, or a missing reason, fails;
* a collection that comes back empty fails in the populated pass — an empty
  array validates against any item schema, so an empty answer is a check that
  looked at nothing. That holds for the arrays NESTED in an envelope, which
  ``COLLECTION_KEYS`` names;
* every read is driven a SECOND time in its emptiest legal state
  (``EMPTY_STATE``). Every null finding in the first wave of this gate was
  there;
* a body is checked in BOTH directions. ``jsonschema`` answers "is every
  declared property satisfied"; an OpenAPI object schema without
  ``additionalProperties`` also claims to ENUMERATE the body, and a key the
  document never mentions is a key no generated client has a field for. That
  half is :func:`_undeclared_keys`, and it is what caught the one defect here.

Runs on every interpreter: it reads the committed schema and never emits.

THE MOUNT. ``codegen_urls.py`` mounts ``video/`` and the module's own
``urls.py`` contributes ``api/v1/``, so the document is written against
``/video/api/v1/…``. ``tests/urls.py`` mounts the same thing, so video is one
of the libraries whose suite was already looking where its document describes
— five of the first eight in this wave were not. The emission mount is
declared here anyway, because
``test_every_declared_path_resolves_under_this_urlconf`` can only hold if the
urlconf under test is this file's own.

What it found on its first run: 14 of 14 operations driven, both states, one
defect.

* ``POST /rooms/{join_code}/lobby/deny`` declares its own REQUEST body as its
  response. ``LobbyDenyView`` carries
  ``@extend_schema(request=LobbyActionRequestSerializer,
  responses={200: LobbyActionRequestSerializer})`` (``views.py:371-373``) and
  ``response_serializer_class = LobbyActionRequestSerializer``
  (``views.py:369``), while the method body returns a hand-built dict:
  ``StapelResponse({"status": "denied", "participant_id": …})``
  (``views.py:382``). So the document publishes ``LobbyActionRequest`` —
  ``participant_id``, and nothing else — for an answer whose whole point is
  the ``status`` field. A generated client's ``deny()`` is typed
  ``LobbyActionRequest`` and has no ``status`` on it; reading
  ``response.status`` is ``undefined``, which is the stapel-alerts defect
  exactly. Its sibling ``LobbyAdmitView`` two blocks up does it correctly
  (``AdmitResponse``, a real response DTO), which is what makes this a slip
  rather than a design.

  Closed in 0.13.0: deny answers the SAME shape admit does — the lobby
  entry's state after the decision, ``DenyResponse {participant}`` — minus
  the token a denied participant is never minted. The recipe below asserts
  that state as well as the schema, because the schema alone would accept
  any lobby row.

  Worth stating because it explains why this file carries a second check:
  plain ``jsonschema`` validation passes this body. ``participant_id`` is
  present and a string, and an OpenAPI schema without
  ``additionalProperties: false`` does not forbid extra keys — so the lie is
  invisible to the validator and perfectly visible to a client generator.
  ``_undeclared_keys`` is the half that sees it.

Everything else holds, in both states — including every ``nullable`` field of
``CallResponse``, ``JoinResponse``, ``ActiveCallResponse`` and
``ParticipantListResponse`` — and ``test_the_gate_is_not_blind`` proves that
is a finding rather than a gate that never looked: it re-drives every honest
operation with its declared schema swapped for ``{"type": "string"}`` and
requires all of them to fail.
"""
import copy
import json
import re
import uuid
from pathlib import Path

import jsonschema
import pytest
from django.test import override_settings
from django.urls import include, path as url_path
from rest_framework.test import APIClient

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs" / "schema.json").read_text())

#: The mount the contract is emitted at (``codegen_urls.py``), reproduced for
#: the test client rather than borrowed from ``tests/urls.py``: a gate that
#: inherits the suite's mount cannot notice when the suite's mount is wrong.
urlpatterns = [
    url_path("video/", include("stapel_video.urls")),
]

pytestmark = [pytest.mark.django_db, pytest.mark.urls(__name__)]

V1 = "/video/api/v1"

#: The provider seam plus the two gates that are about OTHER modules.
#:
#: ``CALL_AUTHORIZER`` defaults to asking stapel-chat whether two people share
#: a conversation, and ``CALL_THREAD_MESSAGE_FUNCTION`` / ``CALL_NOTIFY_ON_RING``
#: reach for chat and notifications on every ring. None of the three is a
#: response shape; all three are seams a deployment wires, and this module's
#: own call suite opens them the same way and for the same reason.
VIDEO_SETTINGS = {
    "VIDEO_PROVIDER": "stapel_video.tests.fakeprovider.FakeProvider",
    "CALL_AUTHORIZER": "stapel_video.calls.authorize.allow_any",
    "CALL_THREAD_MESSAGE_FUNCTION": "",
    "CALL_NOTIFY_ON_RING": False,
    # Not a response shape, and one process-wide locmem bucket shared with
    # every other module in the run.
    "USAGE_THROTTLE": None,
}


@pytest.fixture(autouse=True)
def _seams_and_gates():
    with override_settings(STAPEL_VIDEO=VIDEO_SETTINGS):
        yield


@pytest.fixture(autouse=True)
def _media_root(tmp_path):
    """Nothing here writes files today; pin the root so nothing ever does.

    ``MEDIA_ROOT`` is unset in this module's harness settings, so it defaults
    to the working directory — in stapel-auth that put a data export into the
    checkout, where a stray directory then shadowed a real module. This module
    hands recordings off to stapel-recordings rather than storing them, but a
    single future write would land the ordinary way.
    """
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield


@pytest.fixture(autouse=True)
def _clear_cache():
    """Mandate and capability verdicts are both cached per user for 30 s."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# The contract side: what the document declares
# ─────────────────────────────────────────────────────────────────────────────


def _json_schema(node):
    """OpenAPI 3.0 → JSON Schema, for the divergences that matter here.

    OAS 3.0 spells "may be null" as ``nullable: true`` beside a ``type`` (or
    beside an ``allOf`` wrapping a ``$ref``, which is how
    ``ActiveCallResponse.call`` is emitted); JSON Schema has no such keyword
    and would refuse the null — which is exactly what that field answers when
    nobody is in a call, the state the empty pass exists for. Everything else
    drf-spectacular emits here (``$ref``, ``allOf``, ``required``, ``format``)
    is JSON Schema as written.
    """
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _validator(response_schema):
    root = copy.deepcopy(response_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(_json_schema(root))


def _operations():
    """Every ``(method, path, 2xx code, JSON body schema)`` the contract declares."""
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for code, response in op.get("responses", {}).items():
                body = (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return sorted(ops, key=lambda o: (o[1], o[0], o[2]))


OPERATIONS = _operations()


# ─────────────────────────────────────────────────────────────────────────────
# The second half of the check: keys the document never mentions
# ─────────────────────────────────────────────────────────────────────────────


def _deref(node):
    """Follow one ``$ref`` into ``components.schemas``."""
    ref = node.get("$ref") if isinstance(node, dict) else None
    if not ref:
        return node
    name = ref.rsplit("/", 1)[-1]
    return SCHEMA["components"]["schemas"].get(name, {})


def _properties_of(node):
    """``(properties, enumerates)`` for one object schema.

    ``enumerates`` is False when the schema declines to be a closed list —
    it has an ``additionalProperties`` of its own (a free-form map, which
    several modules in this fleet use for settings and payload blobs) or it
    is a ``oneOf``/``anyOf`` this walk will not try to choose between. Those
    are skipped rather than guessed at: a false positive here would be worse
    than the miss, because it would teach the next reader to distrust the
    check.
    """
    node = _deref(node)
    if not isinstance(node, dict):
        return {}, False
    if "oneOf" in node or "anyOf" in node:
        return {}, False
    if "additionalProperties" in node:
        return dict(node.get("properties") or {}), False
    properties = dict(node.get("properties") or {})
    for branch in node.get("allOf") or ():
        branch_properties, branch_enumerates = _properties_of(branch)
        properties.update(branch_properties)
        if not branch_enumerates:
            return properties, False
    if not properties:
        return {}, False
    return properties, True


def _undeclared_keys(body, schema, path=()):
    """Keys the received body carries that the declared schema never names.

    ``jsonschema`` answers one half of "does this body match the contract":
    every declared property is there and well typed. The other half is that
    the contract ENUMERATES the body — a client is generated from the
    document, so a key the document does not mention is a key no generated
    type has a field for, and reading it is ``undefined`` at runtime and a
    compile error in a typed client. An OpenAPI schema with ``properties``
    and no ``additionalProperties`` is exactly that claim, and this is what
    checks it.
    """
    found = []
    if isinstance(body, list):
        items = _deref(schema).get("items") if isinstance(_deref(schema), dict) else None
        if items is not None:
            for index, item in enumerate(body):
                found.extend(_undeclared_keys(item, items, path + (index,)))
        return found
    if not isinstance(body, dict):
        return found
    properties, enumerates = _properties_of(schema)
    if enumerates:
        for key in body:
            if key not in properties:
                found.append(".".join(str(part) for part in path + (key,)))
    for key, value in body.items():
        if key in properties:
            found.extend(_undeclared_keys(value, properties[key], path + (key,)))
    return found


# ─────────────────────────────────────────────────────────────────────────────
# The wire side: harness
# ─────────────────────────────────────────────────────────────────────────────


def _unique(prefix):
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def make_user(**kwargs):
    from django.contrib.auth import get_user_model

    defaults = dict(
        username=_unique("wire-"),
        email=f"{_unique('wire-')}@example.com",
        password="wire-contract-password-7",
    )
    defaults.update(kwargs)
    return get_user_model().objects.create_user(**defaults)


def client_for(user):
    """A FRESH client per actor.

    Not a convenience: the lobby and the call recipes act as two people in
    one scenario (a host and a guest, a caller and a callee), and
    re-authenticating one shared client silently changes who every earlier
    handle is.
    """
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_room(host=None, *, access_level="public", admit_required=False, scope_key=""):
    from stapel_video import services

    room = services.create_room(
        host or make_user(),
        scope_key=scope_key,
        access_level=access_level,
        admit_required=admit_required,
    )
    return room


def waiting_guest(room):
    """A guest who asked to join a lobbied room and is waiting on the host."""
    from stapel_video.models import RoomParticipant

    guest = make_user()
    response = client_for(guest).post(f"{V1}/rooms/{room.join_code}/join", {}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "waiting", response.content
    return guest, RoomParticipant.objects.get(room=room, user=guest)


def place_call(caller=None, callee=None):
    """A ringing call, placed through the real endpoint."""
    caller = caller or make_user()
    callee = callee or make_user()
    response = client_for(caller).post(
        f"{V1}/calls",
        {"callee_id": str(callee.pk), "thread_key": "wire-thread", "media": "video"},
        format="json",
    )
    assert response.status_code == 201, response.content
    return caller, callee, response.json()["call"]["id"]


def accepted_call():
    """A call in progress: rung, picked up, both parties holding tokens."""
    caller, callee, call_id = place_call()
    response = client_for(callee).post(
        f"{V1}/calls/{call_id}/accept", {"client_session_id": "tab-2"}, format="json"
    )
    assert response.status_code == 200, response.content
    return caller, callee, call_id


class usage_seam:
    """Stand in for the two comm Functions a workspace-bearing host provides.

    ``stapel-video`` never imports stapel-workspaces: ``usage.may_read_scope``
    asks ``workspaces.check_mandate`` (whose mere presence is what makes the
    deployment non-standalone) and ``workspaces.check_capability`` by name.
    Both are SEAMS a deployment wires, so the gate wires them the way a
    deployment does and everything on this side — the 404-not-403 refusal,
    the rollup, the month cut — runs for real.

    Registered per call and restored on exit: a function name has exactly one
    provider, and ``FunctionRegistry.register`` refuses a second.
    """

    MANDATE = "workspaces.check_mandate"
    CAPABILITY = "workspaces.check_capability"

    def __init__(self, user, scope_key):
        self.user = user
        self.scope_key = scope_key

    def __enter__(self):
        from stapel_core.comm.registry import function_registry
        from stapel_core.django.mandate import MANDATE_RESULT_KEY

        self._previous = {}
        for name in (self.MANDATE, self.CAPABILITY):
            self._previous[name] = function_registry._providers.pop(name, None)
            function_registry._schemas.pop(name, None)

        user_id, scope_key = str(self.user.pk), str(self.scope_key)
        function_registry.register(
            self.MANDATE, lambda payload: {MANDATE_RESULT_KEY: True}
        )
        function_registry.register(
            self.CAPABILITY,
            lambda payload: {
                "allowed": (
                    str(payload.get("workspace_id")) == scope_key
                    and str(payload.get("user_id")) == user_id
                ),
                "role": "admin",
            },
        )
        return self

    def __exit__(self, *exc):
        from stapel_core.comm.registry import function_registry

        for name, previous in self._previous.items():
            function_registry._providers.pop(name, None)
            function_registry._schemas.pop(name, None)
            if previous is not None:
                function_registry._providers[name] = previous
        return False


def presence_span(scope_key, user_id, *, minutes=30, room="abc-defg-hij"):
    """One closed presence span inside the current month, in ``scope_key``."""
    from datetime import timedelta

    from stapel_video import presence

    month = presence.recent_months(1)[0]
    start = presence.month_bounds(month, "UTC")[0] + timedelta(hours=1)
    presence.open_span(
        room_key=room,
        user_id=str(user_id),
        connection_id=_unique("conn-"),
        joined_at=start,
        closed_at=start + timedelta(minutes=minutes),
        close_reason="webhook",
        scope_key=scope_key,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The recipe table
# ─────────────────────────────────────────────────────────────────────────────


class Call:
    """Performs one declared operation, and refuses to guess a path parameter."""

    def __init__(self, method, path):
        self.method = method
        self.path = path

    def __call__(self, client, params=None, data=None, query="", **extra):
        url = self.path
        for name, value in (params or {}).items():
            url = url.replace("{%s}" % name, str(value))
        assert "{" not in url, (
            f"{self.method} {self.path}: a path parameter this gate does not "
            "know how to fill — teach its recipe, or the operation goes unchecked"
        )
        send = getattr(client, self.method.lower())
        if self.method in ("GET", "DELETE"):
            return send(url + query, **extra)
        return send(url + query, data if data is not None else {}, format="json", **extra)


#: How to perform each operation the contract declares with a JSON response
#: body, keyed by ``(METHOD, path template, status code)``. ``code`` is
#: ``None`` for the usual case of one 2xx per operation.
RECIPES = {}

#: The same operations again, in the emptiest state the contract still has to
#: describe: no rows, or the one row the operation addresses carrying none of
#: its optional values. A populated answer cannot say what a field holds when
#: there is nothing to hold, and that is where every null finding in the first
#: wave of this gate was.
EMPTY_STATE = {}


def recipe(method, path, code=None, table=None):
    def register(fn):
        target = RECIPES if table is None else table
        key = (method, V1 + path, code)
        assert key not in target, f"duplicate recipe for {method} {path} {code}"
        target[key] = fn
        return fn

    return register


def empty_state(method, path, code=None):
    return recipe(method, path, code, table=EMPTY_STATE)


#: Operations that cannot be driven in-process, by name and with the reason.
#:
#: EMPTY. Every operation this module declares is reachable from a test
#: client. The provider is a SEAM resolved by dotted path from settings, so
#: the gate points it at this module's own ``FakeProvider`` exactly the way a
#: deployment points it at a real media server — and the token minting, the
#: state machine, the lobby and the presence rollup all run for real on this
#: side of it. ``POST /webhook`` declares no JSON body at all (``responses=
#: {200: None}``) and is therefore not an operation here.
UNDRIVABLE: dict = {}

#: Where the rows live, for operations whose collection is NESTED in an
#: envelope. An empty array validates against any item schema, so the
#: populated pass has to insist the array actually carried something.
COLLECTION_KEYS = {
    ("GET", V1 + "/rooms/{join_code}/participants"): ("items",),
    ("GET", V1 + "/scopes/{scope_key}/usage/"): ("months",),
}


# ── rooms ────────────────────────────────────────────────────────────────────


@recipe("POST", "/rooms")
def _room_create(call):
    return call(
        client_for(make_user()),
        data={"access_level": "public", "admit_required": False,
              "client_session_id": "tab-1"},
    )


@recipe("GET", "/rooms/{join_code}")
def _room_detail(call):
    """The host's own read — ``scope_key`` revealed, because they belong."""
    host = make_user()
    room = make_room(host, scope_key="wire-workspace")
    return call(client_for(host), params={"join_code": room.join_code})


@empty_state("GET", "/rooms/{join_code}")
def _room_detail_empty(call):
    """A stranger holding the code, on an unpartitioned host.

    Two emptinesses at once, and both are deliberate: the deployment has no
    scope to name, and the reader is not a participant, so ``scope_key`` is
    redacted to ``""`` rather than merely being empty. Every declared string
    of ``RoomResponse`` is REQUIRED, so an empty one still has to be sent.
    """
    room = make_room(scope_key="")
    return call(client_for(make_user()), params={"join_code": room.join_code})


@recipe("POST", "/rooms/{join_code}/join")
def _room_join(call):
    """An open room: the guest is admitted straight away and gets a token."""
    room = make_room(access_level="public", admit_required=False)
    return call(
        client_for(make_user()),
        params={"join_code": room.join_code},
        data={"client_session_id": "tab-3"},
    )


@recipe("GET", "/rooms/{join_code}/participants")
def _room_participants(call):
    host = make_user()
    room = make_room(host, access_level="restricted", admit_required=True)
    waiting_guest(room)
    return call(client_for(host), params={"join_code": room.join_code})


@empty_state("GET", "/rooms/{join_code}/participants")
def _room_participants_empty(call):
    """A brand-new room, read by its host: one row, no page either side.

    ``next_anchor`` and ``prev_anchor`` are both null here — the state the
    two declared nullables exist for — and that is a page boundary a client's
    "load more" reads on every first frame. The list itself cannot be empty:
    the creator is auto-admitted as host, and a caller who is not a
    participant is refused outright.
    """
    host = make_user()
    room = make_room(host)
    return call(client_for(host), params={"join_code": room.join_code})


@recipe("POST", "/rooms/{join_code}/lobby/admit")
def _lobby_admit(call):
    host = make_user()
    room = make_room(host, access_level="restricted", admit_required=True)
    _guest, participant = waiting_guest(room)
    return call(
        client_for(host),
        params={"join_code": room.join_code},
        data={"participant_id": str(participant.id)},
    )


@recipe("POST", "/rooms/{join_code}/lobby/deny")
def _lobby_deny(call):
    host = make_user()
    room = make_room(host, access_level="restricted", admit_required=True)
    _guest, participant = waiting_guest(room)
    return call(
        client_for(host),
        params={"join_code": room.join_code},
        data={"participant_id": str(participant.id)},
    )


# ── the usage read ───────────────────────────────────────────────────────────


@recipe("GET", "/scopes/{scope_key}/usage/")
def _scope_usage(call):
    reader = make_user()
    scope_key = _unique("wire-workspace-")
    presence_span(scope_key, reader.pk, minutes=30)
    with usage_seam(reader, scope_key):
        return call(
            client_for(reader), params={"scope_key": scope_key}, query="?months=1"
        )


@empty_state("GET", "/scopes/{scope_key}/usage/")
def _scope_usage_empty(call):
    """A partition the caller may read and nobody has talked in.

    Not a 404: once the registry has said yes, "no calls" is a real answer,
    and conflating it with the refusal would make an idle month look like a
    permissions bug. Every month bucket comes back with ``users: []``.
    """
    reader = make_user()
    scope_key = _unique("wire-workspace-")
    with usage_seam(reader, scope_key):
        return call(
            client_for(reader), params={"scope_key": scope_key}, query="?months=2"
        )


# ── 1:1 calls ────────────────────────────────────────────────────────────────


@recipe("POST", "/calls")
def _call_create(call):
    callee = make_user()
    return call(
        client_for(make_user()),
        data={
            "callee_id": str(callee.pk),
            "thread_key": "wire-thread",
            "media": "video",
            "client_session_id": "tab-1",
        },
    )


@recipe("GET", "/calls/{call_id}")
def _call_detail(call):
    """A finished call: answered, ended, with a duration and a reason.

    The populated state of ``CallResponse`` is the one where ``answered_at``
    and ``ended_at`` are set and ``expires_at`` is null; the ringing state is
    its mirror, and both are reached by the other recipes here.
    """
    caller, callee, call_id = accepted_call()
    assert client_for(caller).post(
        f"{V1}/calls/{call_id}/hangup", {}, format="json"
    ).status_code == 200
    return call(client_for(caller), params={"call_id": call_id})


@empty_state("GET", "/calls/{call_id}")
def _call_detail_empty(call):
    """A call that is still ringing: nothing has happened to it yet.

    ``answered_at`` and ``ended_at`` are both null, ``end_reason`` is the
    empty string it is documented to be "while it has not" stopped, and
    ``duration_seconds`` is zero. Every null this shape can carry is carried
    here, which is the state the first wave of this gate found every one of
    its lies in.
    """
    caller, _callee, call_id = place_call()
    return call(client_for(caller), params={"call_id": call_id})


@recipe("GET", "/calls/active")
def _call_active(call):
    caller, _callee, _call_id = place_call()
    return call(client_for(caller))


@empty_state("GET", "/calls/active")
def _call_active_empty(call):
    """Nobody in a call — ``call`` is present and null.

    The declared shape is ``{call: CallResponse | null}`` rather than a 404
    on purpose, and null is the answer the front reads on every mount and
    every socket reconnect. A gate that only ever asked while a call was up
    would never see it.
    """
    return call(client_for(make_user()))


@recipe("POST", "/calls/{call_id}/accept")
def _call_accept(call):
    _caller, callee, call_id = place_call()
    return call(
        client_for(callee),
        params={"call_id": call_id},
        data={"client_session_id": "tab-2"},
    )


@recipe("POST", "/calls/{call_id}/decline")
def _call_decline(call):
    _caller, callee, call_id = place_call()
    return call(client_for(callee), params={"call_id": call_id})


@recipe("POST", "/calls/{call_id}/hangup")
def _call_hangup(call):
    caller, _callee, call_id = accepted_call()
    return call(client_for(caller), params={"call_id": call_id})


@recipe("POST", "/calls/{call_id}/token")
def _call_token(call):
    """The re-mint a full reconnect needs. Live calls only, by design."""
    caller, _callee, call_id = accepted_call()
    return call(
        client_for(caller),
        params={"call_id": call_id},
        data={"client_session_id": "tab-1"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────


#: Operations whose declared body the wire does not send. Each entry names the
#: defect AND its owner, and ``strict=True`` turns a fixed one into a failure
#: until the entry is deleted — so a finding can be neither forgotten nor
#: quietly kept. Recorded, not fixed: this is a gate.
KNOWN_MISMATCHES: dict = {}

#: Which pass each recorded mismatch applies to.
#:
#: A defect that shows in only ONE state must not xfail the other: with
#: ``strict=True`` an honest answer marked xfail is itself a failure, and
#: marking both passes would be a claim this gate has not made. Anything not
#: named here applies to both. ``lobby/deny`` is a write with no empty-state
#: recipe, so it needs no narrowing.
MISMATCH_STATES: dict = {}

_ALL_STATES = frozenset({"populated", "empty"})


def _mismatch_reason(method, path, state):
    """The recorded reason if this operation lies in THIS state, else None."""
    key = (method, path)
    if key not in KNOWN_MISMATCHES:
        return None
    if state not in MISMATCH_STATES.get(key, _ALL_STATES):
        return None
    return KNOWN_MISMATCHES[key]


def _recipe_for(table, method, path, code):
    """The code-specific recipe if there is one, else the operation's."""
    return table.get((method, path, code)) or table.get((method, path, None))


def test_the_contract_declares_something_to_check():
    assert OPERATIONS, "docs/schema.json declares no JSON responses at all"


def test_every_declared_path_resolves_under_this_urlconf():
    """The suite must be looking where the document describes.

    Five of the first eight libraries this gate was written for had a
    committed contract that nothing had ever driven, because the test urlconf
    mounted somewhere the document does not describe: a prefix one segment
    short, the paths bare, less than the emission, both segments skipped, a
    doubled prefix. In every case the operations were "covered" by a file that
    could not have reached a single one of them.

    That is the same family as a gate nobody asks: the recipes can all be
    written, the run can be green, and not one request went where the contract
    says it goes. A missing recipe already fails loudly; this fails when the
    MOUNT is wrong, which no per-operation check can see, because when the
    mount is wrong every operation is equally and silently unreachable.

    Asserted against the urlconf THIS module declares — inheriting the suite's
    mount would be exactly the blindness the check exists to remove.
    """
    from django.urls import Resolver404, resolve

    # Resolution cares about the SHAPE of a segment, and this URL set mixes a
    # uuid converter with two free-form string ones. A path counts as
    # reachable if any one shape resolves: the question here is whether the
    # mount exists, not whether a particular id does.
    candidates = (
        "00000000-0000-4000-8000-000000000000",
        "1",
        "a-slug",
    )

    unreachable = []
    for _method, path, _code, _schema in OPERATIONS:
        for value in candidates:
            try:
                resolve(re.sub(r"\{[^}]+\}", value, path))
                break
            except Resolver404:
                continue
        else:
            unreachable.append(path)

    assert not unreachable, (
        "these declared paths do not resolve under this module's urlconf, so "
        "nothing here can be driving them — the mount is wrong, not the "
        "recipes:\n  " + "\n  ".join(sorted(set(unreachable)))
    )


def test_every_declared_operation_is_driven_or_named_undrivable():
    """No operation is covered by silence, and no entry outlives its operation."""
    missing = [
        (method, path, code)
        for method, path, code, _schema in OPERATIONS
        if _recipe_for(RECIPES, method, path, code) is None
        and (method, path) not in UNDRIVABLE
    ]
    assert not missing, (
        "operations with a declared JSON response body and no recipe:\n"
        + "\n".join(f"  {m} {p} -> {c}" for m, p, c in missing)
    )

    declared_codes = {(m, p, c) for m, p, c, _ in OPERATIONS}
    declared_ops = {(m, p) for m, p, _c, _ in OPERATIONS}
    stale = sorted(
        key
        for key in RECIPES
        if (key[0], key[1]) not in declared_ops
        or (key[2] is not None and key not in declared_codes)
    )
    assert not stale, (
        "recipes for operations/status codes the contract no longer declares:\n"
        + "\n".join(f"  {m} {p} -> {c}" for m, p, c in stale)
    )
    stale_exclusions = sorted(set(UNDRIVABLE) - declared_ops)
    assert not stale_exclusions, (
        f"exclusions for operations the contract no longer declares: {stale_exclusions}"
    )
    both = sorted((m, p) for m, p, _c in RECIPES if (m, p) in UNDRIVABLE)
    assert not both, f"driven AND excluded: {both}"
    for key, reason in UNDRIVABLE.items():
        assert reason and reason.strip(), f"{key} is excluded with no reason"

    # RECIPES ∪ UNDRIVABLE is EXACTLY the declared set — asserted as sets, so
    # neither an operation nobody drives nor an entry nobody needs survives.
    covered = {(m, p) for m, p, _c in RECIPES} | set(UNDRIVABLE)
    assert covered == declared_ops, (
        "RECIPES ∪ UNDRIVABLE is not the declared set:\n"
        f"  declared but uncovered: {sorted(declared_ops - covered)}\n"
        f"  covered but undeclared: {sorted(covered - declared_ops)}"
    )

    stale_collections = sorted(set(COLLECTION_KEYS) - declared_ops)
    assert not stale_collections, (
        f"COLLECTION_KEYS names operations the contract no longer declares: "
        f"{stale_collections}"
    )


def test_every_read_is_also_driven_in_its_emptiest_state():
    """A populated answer cannot say what a field holds when there is nothing.

    Every null finding in the first wave of this gate was on the empty state.
    A gate that only ever seeds three rows and asks never sees any of them.
    """
    exempt: set = set()
    reads = {
        (method, path)
        for method, path, _code, _schema in OPERATIONS
        if method == "GET"
    }
    covered = {(m, p) for m, p, _c in EMPTY_STATE}
    missing = sorted(reads - covered - exempt)
    assert not missing, (
        "reads driven only against a populated database — the state where "
        "every null claim in this gate's history was found is unchecked:\n"
        + "\n".join(f"  {m} {p}" for m, p in missing)
    )
    declared_ops = {(m, p) for m, p, _c, _ in OPERATIONS}
    stale = sorted({(m, p) for m, p, _c in EMPTY_STATE} - declared_ops)
    assert not stale, f"empty-state recipes for undeclared operations: {stale}"


def test_every_known_mismatch_is_still_declared_and_explained():
    """A recorded defect must name a live operation and carry its reason.

    Without this, an operation that is renamed or removed leaves an entry that
    silences nothing and reads like a known problem forever.
    """
    declared = {(method, path) for method, path, _code, _schema in OPERATIONS}
    for key, reason in KNOWN_MISMATCHES.items():
        assert key in declared, (
            f"{key} is recorded as a known mismatch but the contract no longer "
            "declares it — delete the entry"
        )
        assert reason and reason.strip(), f"{key} is recorded with no reason"

    stale_states = sorted(set(MISMATCH_STATES) - set(KNOWN_MISMATCHES))
    assert not stale_states, (
        f"MISMATCH_STATES narrows operations that are not recorded as "
        f"mismatches at all: {stale_states}"
    )
    for key, states in MISMATCH_STATES.items():
        assert states and states <= _ALL_STATES, (
            f"{key} is narrowed to {sorted(states)}, which is not a subset of "
            f"{sorted(_ALL_STATES)} — an empty or unknown set silences nothing"
        )


def _drive(table, method, path, code, body_schema, *, expect_rows):
    perform = _recipe_for(table, method, path, code)
    assert perform is not None, (
        f"{method} {path} declares a response body and has no recipe — an "
        "unchecked operation is a schema nobody proves. Teach RECIPES, or "
        "name it in UNDRIVABLE with a reason."
    )

    response = perform(Call(method, path))
    assert response.status_code == code, (
        f"{method} {path}: expected the declared {code}, got "
        f"{response.status_code}: {response.content[:400]}"
    )

    body = response.json()
    errors = sorted(_validator(body_schema).iter_errors(body), key=lambda e: list(e.path))
    assert not errors, (
        f"{method} {path} answers a body the contract does not describe:\n"
        + "\n".join(f"  at {list(e.path) or '<root>'}: {e.message}" for e in errors[:10])
        + f"\n  body: {json.dumps(body)[:600]}"
    )
    # The other direction: a key the document never mentions is a key no
    # generated client has a field for.
    undeclared = _undeclared_keys(body, body_schema)
    assert not undeclared, (
        f"{method} {path} answers keys the contract never mentions, so no "
        f"generated client has a field for them: {sorted(undeclared)}"
        + f"\n  body: {json.dumps(body)[:600]}"
    )
    # An empty list validates against any item schema, so a collection must
    # actually carry a row for the check to have looked at anything — both the
    # top-level arrays and the ones nested in an envelope.
    if expect_rows:
        if isinstance(body, list):
            assert body, f"{method} {path}: the declared collection came back empty"
        if isinstance(body, dict):
            for key in COLLECTION_KEYS.get((method, path), ()):
                assert body.get(key), (
                    f"{method} {path}: the declared {key!r} collection came "
                    "back empty, so its item schema was never looked at"
                )
    return body


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    OPERATIONS,
    ids=[f"{m} {p} {c}" for m, p, c, _ in OPERATIONS],
)
def test_the_wire_matches_the_declared_response(method, path, code, body_schema, request):
    if (method, path) in UNDRIVABLE:
        pytest.skip(f"excluded by name: {UNDRIVABLE[(method, path)]}")

    reason = _mismatch_reason(method, path, "populated")
    if reason is not None:
        request.node.add_marker(
            pytest.mark.xfail(strict=True, reason=f"{method} {path}: {reason}")
        )

    _drive(RECIPES, method, path, code, body_schema, expect_rows=True)


_EMPTY_OPERATIONS = [
    (method, path, code, schema)
    for method, path, code, schema in OPERATIONS
    if _recipe_for(EMPTY_STATE, method, path, code) is not None
]


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    _EMPTY_OPERATIONS,
    ids=[f"{m} {p} {c}" for m, p, c, _ in _EMPTY_OPERATIONS],
)
def test_the_wire_matches_the_declared_response_when_there_is_nothing_there(
    method, path, code, body_schema, request
):
    """The same claim, asked in the state where the nulls live."""
    reason = _mismatch_reason(method, path, "empty")
    if reason is not None:
        request.node.add_marker(
            pytest.mark.xfail(strict=True, reason=f"{method} {path}: {reason}")
        )

    _drive(EMPTY_STATE, method, path, code, body_schema, expect_rows=False)


def test_the_undeclared_key_check_is_not_blind():
    """The enumeration half, canaried the way the validation half is.

    ``_undeclared_keys`` is the check that caught ``lobby/deny``, and a check
    that silently returns an empty list for every input would look exactly
    like a clean run. So: give it a real body and a schema that enumerates
    only one of its keys, and require it to name the rest.
    """
    schema = {"type": "object", "properties": {"kept": {"type": "string"}}}
    body = {"kept": "yes", "extra": 1, "another": None}
    assert sorted(_undeclared_keys(body, schema)) == ["another", "extra"]

    # A schema that declines to enumerate (a free-form map) reports nothing,
    # and a nested object is walked rather than skipped.
    assert _undeclared_keys(body, {"type": "object", "additionalProperties": {}}) == []
    nested = {
        "type": "object",
        "properties": {"inner": {"type": "object", "properties": {}}},
    }
    assert _undeclared_keys({"inner": {"surprise": 1}}, nested) == []


def test_the_gate_is_not_blind():
    """A canary: swap a declared schema for one the wire cannot satisfy.

    Everything above can be green for two reasons — the claims are honest, or
    the check never looks at the body. This tells them apart by validating a
    real response against ``{"type": "string"}``: every operation here answers
    an object, so every one of them must fail. If any passes, the validation
    in ``_drive`` is not reaching the received body and this whole file proves
    nothing.
    """
    honest = [
        (method, path, code)
        for method, path, code, _schema in OPERATIONS
        if (method, path) not in KNOWN_MISMATCHES and (method, path) not in UNDRIVABLE
    ]
    assert honest, "nothing left to canary"

    survivors = []
    for method, path, code in honest:
        try:
            _drive(RECIPES, method, path, code, {"type": "string"}, expect_rows=False)
        except AssertionError:
            continue
        survivors.append(f"{method} {path}")
    assert not survivors, (
        "these operations passed validation against {'type': 'string'} — the "
        "gate is not looking at the body it received:\n  " + "\n  ".join(survivors)
    )
