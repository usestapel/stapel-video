"""The scope dimension: grant -> span, the rollup, the month cut, the gate.

Five claims, each of which is a defect the moment it stops holding.

1. **Propagation.** ``scope_key`` is known only at the grant, so if the grant
   does not carry it and the provider does not echo it, the whole feature is
   a column full of NULLs and a report that says nobody talked.
2. **Union.** The rollup must produce the same seconds as the aggregate the
   invoice is drawn from. Two implementations of "how long was this person
   present" is two numbers, and the discrepancy surfaces in a customer's
   dispute rather than in CI.
3. **DST.** Month boundaries are LOCAL midnight. A naive cut puts an hour of
   March into April, twice a year, for every workspace that is not on UTC.
4. **The gate.** Holding a mandate somewhere is not authority over a
   workspace id somebody typed into a URL — and the refusal has to be a 404,
   because a 403 confirms the id is real.
5. **Backfill idempotency.** The population is "scope_key IS NULL", so a
   second run is a no-op and a crashed run resumes. A backfill that is not
   idempotent is a backfill nobody dares re-run.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from stapel_core.comm import call, function_registry
from stapel_core.django.mandate import MANDATE_FUNCTION, MANDATE_RESULT_KEY

from stapel_video import presence, services
from stapel_video.models import ParticipantSpan
from stapel_video.providers.base import METADATA_SCOPE_KEY
from stapel_video.tests.fakeprovider import FakeProvider

pytestmark = pytest.mark.django_db

SCOPE_A = "workspace-a"
SCOPE_B = "workspace-b"
ROOM = "abc-defg-hij"
OTHER_ROOM = "zzz-zzzz-zzz"
ALICE = "user-a"
BOB = "user-b"
T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

CAPABILITY_FUNCTION = "workspaces.check_capability"


def span(user, *, scope=SCOPE_A, room=ROOM, start=0, minutes=60, connection=None, at=None):
    joined = (at or T0) + timedelta(minutes=start)
    return presence.open_span(
        room_key=room,
        user_id=user,
        connection_id=connection or f"{user}-{room}-{start}",
        joined_at=joined,
        closed_at=joined + timedelta(minutes=minutes),
        close_reason="webhook",
        scope_key=scope,
    )[0]


# ---------------------------------------------------------------------------
# 1. The grant carries the scope, and the echo puts it on the span
# ---------------------------------------------------------------------------


def test_the_join_grant_carries_the_room_s_scope(auth_client, other_user):
    """If the grant does not carry it, nothing downstream can: a webhook names
    a room and a person, and never a tenant."""
    room = services.create_room(other_user, scope_key=SCOPE_A, access_level="public")
    FakeProvider.mints = []

    services._mint_token(room, other_user, "sess-1")

    assert FakeProvider.mints[-1][5] == SCOPE_A


def test_an_unpartitioned_host_mints_no_scope_at_all(other_user):
    """`Room.scope_key` is "" for a single-tenant host. Passing that through
    would create a tenant whose id is the empty string."""
    room = services.create_room(other_user, access_level="public")
    FakeProvider.mints = []

    services._mint_token(room, other_user)

    assert FakeProvider.mints[-1][5] is None


def test_the_webhook_echo_lands_on_the_span(api_client):
    """The whole propagation path, end to end, through the ingress view: the
    provider echoes the grant metadata and the presence writer copies it."""
    body = json.dumps(
        {
            "event": "participant_joined",
            "created_at": int(T0.timestamp()),
            "room": {"name": ROOM, "sid": "RM_1"},
            "participant": {
                "identity": f"{ALICE}_laptop",
                "name": "Alice",
                "joined_at": int(T0.timestamp()),
                "metadata": json.dumps({"avatar": "", METADATA_SCOPE_KEY: SCOPE_A}),
            },
        }
    )
    resp = api_client.post(
        "/video/api/v1/webhook", body, content_type="application/json",
        HTTP_AUTHORIZATION="valid",
    )

    assert resp.status_code == 200, resp.data
    assert ParticipantSpan.objects.get(connection_id="laptop").scope_key == SCOPE_A


def test_a_grant_without_a_scope_writes_null_never_empty_string(api_client):
    body = json.dumps(
        {
            "event": "participant_joined",
            "created_at": int(T0.timestamp()),
            "room": {"name": ROOM, "sid": "RM_1"},
            "participant": {
                "identity": f"{ALICE}_laptop",
                "joined_at": int(T0.timestamp()),
                "metadata": json.dumps({"avatar": ""}),
            },
        }
    )
    api_client.post(
        "/video/api/v1/webhook", body, content_type="application/json",
        HTTP_AUTHORIZATION="valid",
    )

    assert ParticipantSpan.objects.get(connection_id="laptop").scope_key is None


def test_a_redelivery_does_not_move_a_recorded_stay_between_tenants():
    """The span is append-only. A webhook replay carrying a different scope
    must not silently re-invoice somebody else's call."""
    span(ALICE, scope=SCOPE_A, connection="laptop")
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="laptop", joined_at=T0,
        scope_key=SCOPE_B,
    )

    assert ParticipantSpan.objects.get(connection_id="laptop").scope_key == SCOPE_A


# ---------------------------------------------------------------------------
# 2. The rollup: union, distinct rooms, scope isolation
# ---------------------------------------------------------------------------


def test_two_overlapping_connections_are_one_presence_not_two():
    # Laptop 12:00-13:00, phone 12:30-13:30. Present 12:00-13:30 = 90 min.
    span(ALICE, start=0, minutes=60, connection="laptop")
    span(ALICE, start=30, minutes=60, connection="phone")

    rows = presence.usage_rollup(
        scope_key=SCOPE_A, period_start=T0, period_end=T0 + timedelta(days=1)
    )

    assert [row["presence_seconds"] for row in rows] == [90 * 60]
    assert rows[0]["connections"] == 2


def test_the_rollup_agrees_with_the_aggregate_the_invoice_uses():
    span(ALICE, start=0, minutes=60, connection="laptop")
    span(ALICE, start=30, minutes=60, connection="phone")
    span(BOB, start=0, minutes=15)

    end = T0 + timedelta(days=1)
    rows = presence.usage_rollup(scope_key=SCOPE_A, period_start=T0, period_end=end)
    by_user = {row["user_id"]: row["presence_seconds"] for row in rows}
    for user_id, seconds in by_user.items():
        aggregate = presence.presence_aggregate(
            user_id=user_id, period_start=T0, period_end=end
        )
        assert aggregate["presence_seconds"] == seconds


def test_rooms_counts_calls_not_reconnects():
    span(ALICE, room=ROOM, start=0, minutes=10, connection="one")
    span(ALICE, room=ROOM, start=20, minutes=10, connection="two")
    span(ALICE, room=OTHER_ROOM, start=40, minutes=10, connection="three")

    row = presence.usage_rollup(
        scope_key=SCOPE_A, period_start=T0, period_end=T0 + timedelta(days=1)
    )[0]

    assert (row["rooms"], row["connections"]) == (2, 3)


def test_one_tenant_never_sees_another_s_minutes():
    span(ALICE, scope=SCOPE_A, minutes=60)
    span(BOB, scope=SCOPE_B, minutes=60, room=OTHER_ROOM)

    rows = presence.usage_rollup(
        scope_key=SCOPE_A, period_start=T0, period_end=T0 + timedelta(days=1)
    )

    assert [row["user_id"] for row in rows] == [ALICE]


def test_unscoped_spans_belong_to_nobody_s_report():
    """A host that partitions nothing writes NULL, and NULL is not a tenant."""
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="c", joined_at=T0,
        closed_at=T0 + timedelta(hours=1), close_reason="webhook",
    )

    assert presence.usage_rollup(
        scope_key=SCOPE_A, period_start=T0, period_end=T0 + timedelta(days=1)
    ) == []


def test_the_empty_scope_is_refused_not_answered():
    """"" is not "every scope" — answering it would hand one tenant's screen
    the whole instance."""
    with pytest.raises(ValueError):
        presence.usage_rollup(
            scope_key="", period_start=T0, period_end=T0 + timedelta(days=1)
        )


def test_an_unknown_grouping_raises_rather_than_answering_the_default():
    with pytest.raises(ValueError):
        presence.usage_rollup(
            scope_key=SCOPE_A, period_start=T0, period_end=T0 + timedelta(days=1),
            group_by="room",
        )


def test_the_rollup_functions_answer_over_the_bus():
    span(ALICE, minutes=30)

    result = call(
        "video.presence.usage_rollup", {"scope_key": SCOPE_A, "period": "2026-08"}
    )
    assert result["rows"][0]["presence_seconds"] == 30 * 60

    monthly = call(
        "video.presence.usage_rollup_by_month",
        {"scope_key": SCOPE_A, "months": 1, "tz": "UTC"},
    )
    assert monthly["months"][0]["month"] == presence.recent_months(1)[0]


# ---------------------------------------------------------------------------
# 3. Month bucketing across a DST edge
# ---------------------------------------------------------------------------

BERLIN = "Europe/Berlin"


def test_a_local_month_starts_at_local_midnight_not_utc_midnight():
    start, end = presence.month_bounds("2026-08", BERLIN)

    assert start == datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)


def test_the_dst_month_is_an_hour_short_and_that_hour_belongs_to_april():
    """Berlin springs forward on 2026-03-29. March therefore runs from
    23:00Z on Feb 28 to 22:00Z on Mar 31 — 743 hours, not 744 — and a call at
    22:30Z on March 31 is already April for the people in it."""
    start, end = presence.month_bounds("2026-03", BERLIN)
    assert (end - start) == timedelta(hours=743)

    edge = datetime(2026, 3, 31, 22, 30, tzinfo=timezone.utc)
    span(ALICE, at=edge, start=0, minutes=30)

    march = presence.usage_rollup(
        scope_key=SCOPE_A, period_start=start, period_end=end
    )
    april_start, april_end = presence.month_bounds("2026-04", BERLIN)
    april = presence.usage_rollup(
        scope_key=SCOPE_A, period_start=april_start, period_end=april_end
    )

    assert march == []
    assert april[0]["presence_seconds"] == 30 * 60


def test_the_same_instant_lands_in_march_for_a_utc_workspace():
    """The stored instant never moved; only the calendar it is read in did."""
    edge = datetime(2026, 3, 31, 22, 30, tzinfo=timezone.utc)
    span(ALICE, at=edge, start=0, minutes=30)

    start, end = presence.month_bounds("2026-03", "UTC")
    assert presence.usage_rollup(
        scope_key=SCOPE_A, period_start=start, period_end=end
    )[0]["presence_seconds"] == 30 * 60


def test_months_come_back_newest_first_and_empty_ones_are_present():
    months = presence.usage_rollup_by_month(scope_key=SCOPE_A, months=3, tz=BERLIN)

    assert [bucket["month"] for bucket in months] == presence.recent_months(3, BERLIN)
    assert all(bucket["users"] == [] for bucket in months)


def test_months_are_clamped_so_a_query_string_cannot_ask_for_a_millennium():
    assert len(
        presence.usage_rollup_by_month(scope_key=SCOPE_A, months=10_000)
    ) == presence.ROLLUP_MAX_MONTHS


def test_a_zone_nobody_has_heard_of_is_refused():
    with pytest.raises(presence.InvalidTimezone):
        presence.month_bounds("2026-03", "Mars/Olympus_Mons")


# ---------------------------------------------------------------------------
# 4. The HTTP gate
# ---------------------------------------------------------------------------

USAGE_URL = "/video/api/v1/scopes/{}/usage/"


@pytest.fixture
def workspaces_seam():
    """A deployment that CAN ask: the mandate seam plus the access registry.

    Registering ``workspaces.check_mandate`` is what makes the deployment
    non-standalone (``deployment_is_standalone``), and
    ``workspaces.check_capability`` is the registry the per-scope question
    goes to. ``grants`` maps user id -> the scopes they may read.
    """
    state = {"mandated": True, "grants": {}, "raises": None}

    def mandate(payload):
        if state["raises"]:
            raise state["raises"]
        return {MANDATE_RESULT_KEY: state["mandated"]}

    def capability(payload):
        allowed = payload["workspace_id"] in state["grants"].get(
            payload["user_id"], ()
        )
        return {"allowed": allowed, "role": "admin" if allowed else None}

    function_registry.register(MANDATE_FUNCTION, mandate)
    function_registry.register(CAPABILITY_FUNCTION, capability)
    yield state
    function_registry._providers.pop(MANDATE_FUNCTION, None)
    function_registry._providers.pop(CAPABILITY_FUNCTION, None)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Mandate and capability answers are both cached per user for 30s."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def member_of_a(workspaces_seam, api_client, user):
    from rest_framework.test import APIClient

    workspaces_seam["grants"][str(user.pk)] = {SCOPE_A}
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_a_member_reads_their_own_workspace(member_of_a):
    span(ALICE, minutes=30, at=presence.month_bounds(
        presence.recent_months(1)[0], "UTC")[0] + timedelta(hours=1))

    resp = member_of_a.get(USAGE_URL.format(SCOPE_A), {"months": 1})

    assert resp.status_code == 200, resp.data
    assert resp.data["scope_key"] == SCOPE_A
    assert resp.data["months"][0]["users"][0]["presence_seconds"] == 30 * 60


def test_a_member_of_a_asking_about_b_gets_404_not_403(member_of_a):
    """403 would confirm the workspace id is real. Enumerating tenant ids
    must not be a way to acquire a customer list."""
    resp = member_of_a.get(USAGE_URL.format(SCOPE_B))

    assert resp.status_code == 404, resp.data
    assert resp.data["localizable_error"] == "error.404.video_scope_not_found"


def test_a_scope_that_does_not_exist_answers_identically(member_of_a):
    resp = member_of_a.get(USAGE_URL.format("no-such-workspace"))

    assert resp.status_code == 404
    assert resp.data["localizable_error"] == "error.404.video_scope_not_found"


def test_a_scope_with_no_calls_is_an_empty_answer_not_a_404(member_of_a):
    """Once the registry said yes, "no calls" is a real answer. Conflating it
    with the refusal would make an idle month look like a permissions bug."""
    resp = member_of_a.get(USAGE_URL.format(SCOPE_A), {"months": 2})

    assert resp.status_code == 200
    assert [bucket["users"] for bucket in resp.data["months"]] == [[], []]


def test_a_mandate_less_account_is_refused_before_the_scope_is_consulted(
    workspaces_seam, api_client, user
):
    workspaces_seam["mandated"] = False
    workspaces_seam["grants"][str(user.pk)] = {SCOPE_A}
    api_client.force_authenticate(user=user)

    resp = api_client.get(USAGE_URL.format(SCOPE_A))

    assert resp.status_code == 403, resp.data


def test_an_anonymous_caller_is_refused(workspaces_seam, api_client):
    resp = api_client.get(USAGE_URL.format(SCOPE_A))

    assert resp.status_code in (401, 403), resp.data


def test_could_not_ask_is_503_never_a_verdict(workspaces_seam, api_client, user):
    workspaces_seam["raises"] = RuntimeError("workspaces is down")
    api_client.force_authenticate(user=user)

    resp = api_client.get(USAGE_URL.format(SCOPE_A))

    assert resp.status_code == 503, resp.data


def test_a_standalone_deployment_falls_back_to_the_authorizer(auth_client, user):
    """Nothing wired: no registry holds a capability, so USAGE_AUTHORIZER is
    the only authority — staff-only by default."""
    resp = auth_client.get(USAGE_URL.format(SCOPE_A))
    assert resp.status_code == 404, resp.data

    user.is_staff = True
    user.save(update_fields=["is_staff"])
    assert auth_client.get(USAGE_URL.format(SCOPE_A)).status_code == 200


def test_a_host_authorizer_replaces_the_staff_default(settings, auth_client):
    settings.STAPEL_VIDEO = {
        **settings.STAPEL_VIDEO,
        "USAGE_AUTHORIZER": "stapel_video.tests.test_scope_usage.allow_scope_a",
    }

    assert auth_client.get(USAGE_URL.format(SCOPE_A)).status_code == 200
    assert auth_client.get(USAGE_URL.format(SCOPE_B)).status_code == 404


def allow_scope_a(request, scope_key):
    return scope_key == SCOPE_A


def test_a_malformed_month_is_a_400_not_a_500(member_of_a):
    assert member_of_a.get(
        USAGE_URL.format(SCOPE_A), {"month": "August"}
    ).status_code == 400
    assert member_of_a.get(
        USAGE_URL.format(SCOPE_A), {"months": "lots"}
    ).status_code == 400
    assert member_of_a.get(
        USAGE_URL.format(SCOPE_A), {"tz": "Mars/Olympus_Mons"}
    ).status_code == 400


def test_a_single_month_answers_the_same_shape_as_a_range(member_of_a):
    resp = member_of_a.get(USAGE_URL.format(SCOPE_A), {"month": "2026-03"})

    assert resp.status_code == 200
    assert [bucket["month"] for bucket in resp.data["months"]] == ["2026-03"]


def test_the_view_declares_its_gate_and_its_throttle():
    """Pinned, not described: the gate is the whole security property, and a
    permission list is one careless edit away from IsAuthenticated."""
    from stapel_core.django.api.permissions import (
        ANONYMOUS_DENIED,
        HasWorkspaceMandateIfScoped,
    )

    from stapel_video.views import ScopeUsageView, UsageThrottle

    assert HasWorkspaceMandateIfScoped in ScopeUsageView.permission_classes
    assert ScopeUsageView.stapel_anonymous_access == ANONYMOUS_DENIED
    assert ScopeUsageView.throttle_classes == [UsageThrottle]
    assert ScopeUsageView.throttle_scope == "video-scope-usage"


def test_the_throttle_rate_comes_from_this_module_s_namespace(settings):
    """A library that wrote into DEFAULT_THROTTLE_RATES would be changing
    rates the host set for its own endpoints."""
    from stapel_video.views import UsageThrottle

    settings.STAPEL_VIDEO = {**settings.STAPEL_VIDEO, "USAGE_THROTTLE": "7/min"}
    assert UsageThrottle().get_rate() == "7/min"


# ---------------------------------------------------------------------------
# 5. Backfill idempotency
# ---------------------------------------------------------------------------


def _resolver(mapping):
    calls = []

    def resolve(room_key):
        calls.append(room_key)
        return mapping.get(room_key)

    resolve.calls = calls
    return resolve


def test_the_backfill_stamps_only_what_has_no_scope():
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="old", joined_at=T0,
        closed_at=T0 + timedelta(hours=1), close_reason="webhook",
    )
    span(BOB, scope=SCOPE_B, room=OTHER_ROOM, connection="new")

    result = presence.backfill_scope_keys(_resolver({ROOM: SCOPE_A}))

    assert result["spans"] == 1
    assert ParticipantSpan.objects.get(connection_id="old").scope_key == SCOPE_A
    assert ParticipantSpan.objects.get(connection_id="new").scope_key == SCOPE_B


def test_running_it_twice_changes_nothing_and_asks_nothing():
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="old", joined_at=T0,
        closed_at=T0 + timedelta(hours=1), close_reason="webhook",
    )
    presence.backfill_scope_keys(_resolver({ROOM: SCOPE_A}))

    second = _resolver({ROOM: SCOPE_B})
    result = presence.backfill_scope_keys(second)

    assert (result["rooms"], result["spans"]) == (0, 0)
    assert second.calls == [], "a stamped row must have left the population"
    assert ParticipantSpan.objects.get(connection_id="old").scope_key == SCOPE_A


def test_a_room_the_resolver_cannot_place_stays_unscoped_and_is_counted():
    """Some rooms really belong to no tenant. Forcing them into one would
    invent a customer; reporting the count is how an operator tells that from
    a resolver pointed at the wrong table."""
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="a", joined_at=T0,
        closed_at=T0 + timedelta(hours=1), close_reason="webhook",
    )
    presence.open_span(
        room_key=OTHER_ROOM, user_id=BOB, connection_id="b", joined_at=T0,
        closed_at=T0 + timedelta(hours=1), close_reason="webhook",
    )

    result = presence.backfill_scope_keys(_resolver({ROOM: SCOPE_A}))

    assert (result["resolved"], result["unresolved"]) == (1, 1)
    assert ParticipantSpan.objects.get(connection_id="b").scope_key is None


def test_the_backfill_batches_and_a_dry_run_writes_nothing():
    for index in range(5):
        presence.open_span(
            room_key=ROOM, user_id=ALICE, connection_id=f"c{index}",
            joined_at=T0 + timedelta(minutes=index),
            closed_at=T0 + timedelta(minutes=index + 1), close_reason="webhook",
        )

    dry = presence.backfill_scope_keys(
        _resolver({ROOM: SCOPE_A}), batch_size=2, dry_run=True
    )
    assert dry["spans"] == 5
    assert ParticipantSpan.objects.filter(scope_key__isnull=True).count() == 5

    presence.backfill_scope_keys(_resolver({ROOM: SCOPE_A}), batch_size=2)
    assert ParticipantSpan.objects.filter(scope_key=SCOPE_A).count() == 5


def test_the_resolver_s_empty_string_is_no_scope_at_all():
    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="a", joined_at=T0,
        closed_at=T0 + timedelta(hours=1), close_reason="webhook",
    )

    presence.backfill_scope_keys(_resolver({ROOM: ""}))

    assert ParticipantSpan.objects.get(connection_id="a").scope_key is None


def test_the_command_refuses_a_resolver_it_cannot_import():
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError):
        call_command("video_backfill_scope", "--resolver", "nope.not_here")


def test_the_command_runs_the_host_s_resolver():
    from django.core.management import call_command

    presence.open_span(
        room_key=ROOM, user_id=ALICE, connection_id="a", joined_at=T0,
        closed_at=T0 + timedelta(hours=1), close_reason="webhook",
    )

    call_command(
        "video_backfill_scope",
        "--resolver", "stapel_video.tests.test_scope_usage.scope_for_room",
    )

    assert ParticipantSpan.objects.get(connection_id="a").scope_key == SCOPE_A


def scope_for_room(room_key):
    """Stand-in for the host's real resolver (meettoday: Room -> workspace_id)."""
    return SCOPE_A if room_key == ROOM else None
