## What this is

- **Rooms** with human-shareable join codes (`abc-defg-hij`).
- **Admission model** — `public` (anyone with the code joins), `scope_trusted`
  (members of the room's scope join instantly, others wait), `restricted`
  (everyone but the host waits in a lobby).
- **Realtime lobby** over WebSockets (Channels) — `waiting` / `admitted` /
  `denied`, authenticated by the same Stapel JWT stack HTTP uses.
- **Host controls** — admit / deny waiting guests.
- **Provider seam** — one `VideoProvider` ABC (mint join token, create room,
  start/stop recording egress, verify webhook). Swap vendors without forking.
- **Recording** is a *seam*, not a pipeline: `start`/`stop_egress` proxy the
  provider and a `video.egress_ended` comm event carries the storage key to
  [stapel-recordings](https://github.com/usestapel/stapel-recordings) — by
  event, never by import.
- **Presence metering** — per-connection spans fed by the media server's own
  join/leave webhooks (the only departure signal that survives a closed
  laptop), reconciled by a sweeper so a lost webhook cannot bill forever, and
  read back as unioned presence time and a co-presence matrix. Raw seconds, no
  threshold: this instance meters, whatever prices it decides what counts.

Alpha. See [MODULE.md](https://github.com/usestapel/stapel-video/blob/main/MODULE.md) for the agent-facing map of seams.

## Quick start

```bash
pip install stapel-video            # core library
pip install 'stapel-video[livekit]' # + the default LiveKit backend
pip install 'stapel-video[channels]'# + the realtime lobby (WebSockets)
```

```python
# urls.py
path("video/", include("stapel_video.urls"))

# asgi.py (realtime lobby)
from channels.routing import ProtocolTypeRouter, URLRouter
from stapel_core.django.jwt.channels import JWTAuthMiddlewareStack
from stapel_video.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
})
```

## API

| Method | Path | What |
|---|---|---|
| POST | `/video/api/rooms` | Create a room (creator auto-admitted host, with a token) |
| GET | `/video/api/rooms/{join_code}` | Room info |
| POST | `/video/api/rooms/{join_code}/join` | Join → admitted / waiting / denied |
| GET | `/video/api/rooms/{join_code}/participants` | Participants (anchor-paginated) |
| POST | `/video/api/rooms/{join_code}/lobby/admit` | Admit a waiting guest (host-only) |
| POST | `/video/api/rooms/{join_code}/lobby/deny` | Deny a waiting guest (host-only) |
| POST | `/video/api/webhook` | Provider webhook ingress (signed, unauthenticated) |

## Configuration (`STAPEL_VIDEO`)

| Key | Default | What |
|---|---|---|
| `VIDEO_PROVIDER` | `…livekit.LiveKitProvider` | Video backend (dotted path) |
| `SCOPE_PROVIDER` | `…scope.DefaultScopeProvider` | scope_key resolution + membership |
| `DEFAULT_ACCESS_LEVEL` | `restricted` | Access level for a room created without one |
| `DEFAULT_ADMIT_REQUIRED` | `True` | Whether new rooms start with the lobby on |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | `""` | Default-provider credentials |
| `WEBHOOK_HANDLERS` | `{}` | Provider-event → handler, merged over the builtins |
| `PRESENCE_SWEEP_INTERVAL_SECONDS` | `60` | How often open presence spans are reconciled |
| `PRESENCE_SPAN_RETENTION_DAYS` | `400` | When a span is purged (`None` = never) |

`VIDEO_PROVIDER`, `DEFAULT_ACCESS_LEVEL` and `DEFAULT_ADMIT_REQUIRED` are the
three CTO-facing config axes surfaced in `docs/capabilities.json`.

## Presence metering

Turn the provider's webhooks on (they point at `POST /video/api/webhook`), and
schedule the two jobs — a meter without the sweeper measures an upper bound,
not a duration:

```python
from stapel_video.tasks import get_video_beat_schedule

CELERY_BEAT_SCHEDULE = {**get_video_beat_schedule(), ...}
```

Celery is optional: `manage.py video_sweep_presence` and `manage.py
video_purge_spans` are the cron form. Then read the numbers by comm Function:

```python
from stapel_core.comm import call

call("video.presence.aggregate", {"user_id": uid, "period": "2026-08"})
# {"presence_seconds": 5400, "rooms_count": 3, ...} — unioned, so a laptop
# and a phone are one person present, not two.

call("video.presence.pairs_export", {"period": "2026-08", "limit": 500})
# {"rows": [{"room_key", "user_a", "user_b", "co_presence_seconds"}, ...],
#  "cursor": ..., "total": None} — raw overlaps; the "counts as a real
#  conversation" threshold belongs to whoever asks, not to the meter.
```
