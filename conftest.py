def pytest_configure(config):
    from django.conf import settings
    if not settings.configured:
        # Single source of truth for this block lives in _codegen_settings.py so
        # the test harness and the contract-emission harness (make contract) can
        # never drift (contract-pipeline.md §3).
        from stapel_video._codegen_settings import settings_kwargs

        settings.configure(**settings_kwargs())
        import django
        django.setup()

        from stapel_core.comm.schemas import autoload_schemas
        autoload_schemas()


import pytest  # noqa: E402


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user(
        username="alice", email="alice@example.com", password="x"
    )


@pytest.fixture
def other_user(db):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user(
        username="bob", email="bob@example.com", password="x"
    )


@pytest.fixture
def auth_client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


def _collect(topics):
    """Subscribe to *topics* (in-process) and collect the Event envelopes.
    Delivery is synchronous with OUTBOX disabled, so the list is populated by
    the time emit() returns."""
    from stapel_core.comm import action_registry, subscribe_action

    collected = []

    def _handler(event):
        collected.append(event)

    for topic in topics:
        subscribe_action(topic, _handler)
    try:
        yield collected
    finally:
        for topic in topics:
            handlers = action_registry._subscribers.get(topic, [])
            if _handler in handlers:
                handlers.remove(_handler)


@pytest.fixture
def captured_events():
    yield from _collect(["video.egress_ended"])


@pytest.fixture
def presence_events():
    """Every presence fact this module emits, in order."""
    yield from _collect(["video.participant.joined", "video.participant.left"])


@pytest.fixture(autouse=True)
def _clean_provider_state():
    """The FakeProvider keeps its call log and its fake roster on the CLASS
    (tests read it without owning the instance the seam built), so a leftover
    room roster would make the sweeper's behaviour depend on test order."""
    from stapel_video.tests.fakeprovider import FakeProvider

    FakeProvider.live = {}
    yield
    FakeProvider.live = {}
