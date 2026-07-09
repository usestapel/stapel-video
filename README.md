# stapel-video

Video calls for the [Stapel](https://github.com/usestapel) framework — a thin,
provider-agnostic library over a real-time video backend (LiveKit by default).

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

Alpha (`0.1.0`). See [MODULE.md](MODULE.md) for the agent-facing map of seams.

## Install

```bash
pip install stapel-video            # core library
pip install 'stapel-video[livekit]' # + the default LiveKit backend
pip install 'stapel-video[channels]'# + the realtime lobby (WebSockets)
```

## Mount

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

`VIDEO_PROVIDER`, `DEFAULT_ACCESS_LEVEL` and `DEFAULT_ADMIT_REQUIRED` are the
three CTO-facing config axes surfaced in `docs/capabilities.json`.
