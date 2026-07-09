"""Provider webhook ingress: signature check + video.egress_ended emit."""
import json

import pytest

pytestmark = pytest.mark.django_db


def _post(api_client, body: dict, *, auth: str):
    return api_client.post(
        "/video/api/webhook",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=auth,
    )


def test_valid_egress_ended_webhook_emits_event(api_client, captured_events):
    body = {
        "event": "egress_ended",
        "egress_id": "eg_42",
        "status": "EGRESS_COMPLETE",
        "storage_key": "recordings/room-x.mp4",
    }
    resp = _post(api_client, body, auth="Bearer good-signature")
    assert resp.status_code == 200
    assert resp.data["ok"] is True

    assert len(captured_events) == 1
    payload = captured_events[0].payload
    assert payload["egress_id"] == "eg_42"
    assert payload["storage_key"] == "recordings/room-x.mp4"


def test_non_terminal_webhook_does_not_emit(api_client, captured_events):
    body = {"event": "room_started", "egress_id": None}
    resp = _post(api_client, body, auth="Bearer good-signature")
    assert resp.status_code == 200
    assert captured_events == []


def test_unsigned_webhook_rejected(api_client, captured_events):
    resp = _post(api_client, {"event": "egress_ended", "egress_id": "x"}, auth="")
    assert resp.status_code == 400
    assert resp.data["localizable_error"] == "error.400.video_invalid_webhook"
    assert captured_events == []


def test_garbage_body_rejected(api_client, captured_events):
    resp = api_client.post(
        "/video/api/webhook",
        data=b"\x00\x01 not json",
        content_type="application/octet-stream",
        HTTP_AUTHORIZATION="Bearer good-signature",
    )
    assert resp.status_code == 400
    assert captured_events == []


def test_webhook_needs_no_authentication(api_client):
    # No force_authenticate — the endpoint is AllowAny (provider-signed).
    resp = _post(api_client, {"event": "room_started"}, auth="Bearer good-signature")
    assert resp.status_code == 200
