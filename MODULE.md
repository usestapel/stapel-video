# stapel-video — MODULE.md

> Agent-facing map of this module: what it provides, where to extend it without
> forking, and what not to do. Kept in the same PR as any change to a seam. See
> also README.md and CHANGELOG.md.

## What this module provides

- **Room / RoomParticipant** — the generic video-call core. `Room` carries a
  human `join_code` (`abc-defg-hij`), an opaque `scope_key`
  (workspace/org/tenant — the library is scope-agnostic, **no FK to
  Organization/Workspace**), an `access_level`
  (`public`/`scope_trusted`/`restricted`), an `admit_required` lobby switch, a
  `created_by`, and an opaque `provider_room_ref` (the media room on the
  provider). `RoomParticipant` holds an admission `status`
  (`waiting`/`admitted`/`denied`/`left`) and a `role` (`host`/`guest`).
- **Admission model** — `join` resolves the access level: `public` = auto-admit;
  `scope_trusted` = auto-admit members of the scope (via the `SCOPE_PROVIDER`
  seam), others lobby; `restricted` = only the host auto-admits, everyone else
  lobbies. A `denied` guest is sticky (re-join stays denied); a `left` guest is
  auto-readmitted. A host who drops the lobby (`admit_required=False`) lets
  anyone in.
- **Realtime lobby** — a Channels `LobbyConsumer` on
  `stapel_core.django.jwt.channels` (G14): a guest connects to receive live
  `waiting`/`admitted`/`denied` decisions, a host to see arrivals. Auth is the
  same Stapel JWT as HTTP; the consumer additionally enforces room membership.
  Channels is an optional extra — HTTP-only hosts poll instead.
- **Recording egress — a SEAM, not a pipeline.** `start_egress`/`stop_egress`
  proxy the provider; the webhook path emits `video.egress_ended` (with the
  storage key) for stapel-recordings to finalize. This library ships no
  recording pipeline and **imports no recordings model** — integration is the
  comm event only.
- **Presence metering — `ParticipantSpan`.** One connection's stay in one room,
  `[joined_at, left_at)`, keyed on an opaque `room_key`, a `user_id` string
  (no FK) and the `connection_id` half of the provider identity. Fed by the
  media server's own `participant_joined` / `participant_left` webhooks —
  the only departure signal that survives a closed laptop — reconciled by a
  **sweeper** so a lost webhook costs one sweep interval instead of an
  unbounded span, and read back through three comm Functions as unioned
  presence time and a co-presence matrix. Append-only: a closed span is never
  reopened or restated, so a product's grace-window policy cannot silently
  rewrite a billing period. Nothing here prices anything.
- **API** — room create/info/join, participants (anchor-paginated), lobby
  admit/deny (host-only), and a signed provider webhook ingress. DTO/serializer
  seams + OpenAPI (drf-spectacular).
- **comm surface** — emits `video.egress_ended`, `video.participant.joined`
  and `video.participant.left`; provides `video.presence.aggregate`,
  `video.presence.spans_export` and `video.presence.pairs_export`; consumes
  `user.deleted` (GDPR) and `profile.changed` (pushes a renamed person's new
  name onto the connections they already hold — the name is a claim frozen
  inside the join token, so a rename otherwise reaches a live call only on
  reconnect).

## Extension points (fork-free)

### 1. Video backend — `VIDEO_PROVIDER` (dotted path, replace)

A `VideoProvider` (ABC, `providers/base.py`) is the one seam a video vendor
plugs into: `create_room` / `mint_join_token` (mandatory core), the
live-connection pair `rename_participant` / `remove_participant`, the room
metadata pair `get_room_metadata` / `update_room_metadata`, the health probe
`probe_reachable`, `list_participants` (the live roster the presence sweeper
reconciles against), and `start_room_egress` / `stop_room_egress` /
`parse_webhook` (recording). Everything but the core defaults to
`NotImplementedError`, so a token-only backend stays valid.

`parse_webhook` returns the **whole** normalized event — `event`, `event_id`,
`event_ts`, `room`, `participant` (with the identity decomposed into
`user_id` + `connection_id` by the provider that invented the convention),
plus the original four egress keys. Before 0.6.0 it returned only those four,
which is why `participant_joined` / `participant_left` / `room_finished`
arrived at the URL, passed the signature check and were dropped: the media
server was telling us who was in the call and the normalizer was throwing it
away. Timestamps are the provider's server clock, never our receipt time.

The optional half is not decoration: every one of those methods addresses a
LIVE connection or the running media room, and only the provider can, because
it invented the identity convention in `mint_join_token`. A capability missing
from this contract is a capability the product reaches the vendor SDK for
directly — which is how a provider layer gets forked. Direct `livekit` imports
outside `stapel_video` are a fleet lint error (SWAP004, `stapel-swap-lint`),
so a new vendor call lands here and every consumer gets it.

The default is
`LiveKitProvider` behind the `[livekit]` extra — its SDK is imported *lazily*
inside each method, so the default dotted path resolves on a plain install and
only calling a method without the extra raises. Resolve with
`stapel_video.providers.get_video_provider()`. Also a CTO-facing **config axis**
(which vendor carries the calls).

### 2. scope_key + membership — `SCOPE_PROVIDER` (dotted path, replace)

A `ScopeProvider` (`resolve(request) -> scope_key`, `filter(qs, request)`,
`is_member(request, scope_key) -> bool`) resolves the opaque scope and answers
the one question `scope_trusted` needs — *is this user a trusted member?*
A True there skips the lobby and mints a live media token, so the shipped
`DefaultScopeProvider` no longer answers it with `is_authenticated`: it
answers with the third principal state (`stapel_core.django.scope`), and an
account holding no mandate in any workspace waits in the lobby like any other
stranger. Where nothing can answer that question at all, the permissive
single-tenant behaviour stands. A lookup that cannot be answered raises 503 —
a token is not the thing to hand out while the question is open.

Guarded by system checks `E003`/`E004` (importable, correctly typed) and
`E009`/`W002` — running the shipped single-scope provider is an ERROR where
this deployment has workspaces, a warning where it is genuinely standalone. A
host returns e.g. the active `workspace_id` and real membership.

### 3. Live rooms — `LIVE_ROOMS_PROVIDER` (dotted path, replace)

A `LiveRoomsProvider` (`live_rooms.py`) answers the single question the
`profile.changed` subscriber needs: **`live_rooms_for_user(user_id) -> list of
provider_room_ref`**. One method, deliberately — a second one turns the seam
into a shadow repository over Room/Participant, a fourth abstraction every
host has to implement in full to get one subscriber working. A second
subscriber earns a second method when it exists.

The default reads this module's own tables (admitted, not-left participants),
which is right for a host that mounts this module's URL surface — its join
endpoint is what writes those rows. A host that adopted `VIDEO_PROVIDER` and
kept its own Room model writes a ~20-line adapter and points the path at it.

**That default is guarded, not documented.** A deployment where `stapel_video`
is installed, the seam is at its default, and this module's URLs are not
mounted has nothing that can ever write what the default reads: the subscriber
answers "no live rooms" forever and a rename silently never reaches a call.
Empty-because-unconfigured is indistinguishable from empty-because-nobody-is-
on-a-call, so it is closed as a measurement — `stapel_video.E008`, a hard
system-check error naming both remedies. A correctly adapted host passes
without knowing it exists.

### 4. Recording hook — `video.egress_ended` (comm emit)

When a room recording finishes, the webhook path emits `video.egress_ended`
(`{egress_id, status, storage_key}`); stapel-recordings (or any subscriber)
finalizes the upload the egress wrote. **This module creates no recording
resource itself.** Schema: `schemas/emits/video.egress_ended.json`.

### 5. Webhook dispatch — `WEBHOOK_HANDLERS` (**merge** registry)

Which provider event runs which handler, in the fleet's standard three-layer
merge shape (`webhooks.py`): `BUILTIN_WEBHOOK_HANDLERS` ← the settings overlay
`STAPEL_VIDEO["WEBHOOK_HANDLERS"]` (`{event: dotted-path | None to remove}`)
← runtime `register_webhook_handler(event, handler)`.

```python
STAPEL_VIDEO = {
    "WEBHOOK_HANDLERS": {
        "room_finished": "myproject.video.on_room_finished",  # add
        "egress_ended": None,                                  # remove
    },
}
```

A handler takes the normalized event dict and returns nothing; it runs inside
the ingress request and owns its own idempotency. This replaced a hardcoded
`if egress_ended`, which meant the only way to react to a second event was to
fork the ingress or terminate the webhook in product code — and re-implement
signature verification there. An event nothing handles is a quiet 200: a 4xx
would make the provider retry a delivery that was perfectly correct. A broken
overlay entry is `stapel_video.E010` at boot (and skipped-with-a-log at
runtime, so a typo cannot 500 a live webhook).

### 6. Presence metering — spans, the sweeper, and three Query-Functions

`ParticipantSpan` is the unit of truth and everything else is derived. The
three writers, in descending order of trust: the provider's webhooks, the
sweeper, and the host's explicit `presence.close_spans_explicitly` (a leave
button). Ingest is idempotent on `(connection_id, joined_at)` — the provider's
own join timestamp — so at-least-once, out-of-order delivery cannot
double-count anybody, and a `participant_left` that overtakes its
`participant_joined` materializes the whole closed span by itself.

**Schedule both jobs.** A meter without the sweeper is metering an upper
bound, not a duration:

```python
from stapel_video.tasks import get_video_beat_schedule

CELERY_BEAT_SCHEDULE = {**get_video_beat_schedule(), ...}
```

Celery is optional — `stapel_video.tasks.sweep_presence` and
`purge_presence_spans` are plain callables, and `manage.py
video_sweep_presence` / `video_purge_spans` are the cron form.
`stapel_video.W003` / `W004` fire when a host drives a beat schedule with no
entry for either.

The read side (`schemas/functions/`):

| Function | Payload | Answer |
|---|---|---|
| `video.presence.aggregate` | `{user_id\|room_key, period}` | `{presence_seconds, rooms_count, users_count, spans_count, …}` |
| `video.presence.spans_export` | `{cursor, limit, period?}` | `{rows, cursor, total}` — raw spans |
| `video.presence.pairs_export` | `{period, cursor, limit}` | `{rows, cursor, total}` — `(user_a, user_b, room_key, co_presence_seconds)` |

Three rules the numbers depend on:

- **Union, never sum.** A person on a laptop and a phone was present once.
  Summing connection durations bills a second device as a second human.
- **No threshold, anywhere.** "More than 15 minutes counts" is the consumer's
  policy, revised per customer; an export that had already dropped the short
  overlaps could not answer the revised question. Everything is raw seconds.
- **`{rows, cursor, total}`, never `{items}`.** Core's snapshot reader looks
  for `rows` by name — an items-shaped answer rebuilds a consumer's projection
  to EMPTY and reports success doing it.

`pairs_export` is **quadratic in a room's distinct attendees** (a co-presence
matrix has N²/2 cells; a 30-person meeting is 435 pair evaluations, a
500-person webinar ~125k). Rooms are the batch boundary, so memory stays
proportional to one room. That is the shape of the question, not of the
implementation — the mitigation for a broadcast-shaped room is to stop asking
it, not to rewrite the loop.

Retention: `PRESENCE_SPAN_RETENTION_DAYS` (400) deletes spans by `joined_at`.
This module keeps **no rollup table** — the aggregate is computed from spans
every time, which is the only way it stays correct while the sweeper is still
closing yesterday's zombies. A host needing numbers older than the window
snapshots them through `spans_export`.

GDPR: `ParticipantSpan.user_id` is a `CharField`, not a FK, so erasure is a
decision rather than a cascade. `VideoGDPRProvider.delete` **pseudonymizes**
it (a keyed digest) instead of deleting the rows: the person goes, the
counters, distinct counts and pair overlaps do not move, and closed reporting
periods are not silently restated.

### Settings — `STAPEL_VIDEO` namespace (`conf.py`)

Resolution order per key: `settings.STAPEL_VIDEO[key]` -> flat Django setting ->
environment variable -> default. Read lazily at call time.

| Key | Default | Semantics |
|---|---|---|
| `VIDEO_PROVIDER` | `…livekit.LiveKitProvider` | **axis** + seam (dotted path) |
| `SCOPE_PROVIDER` | `…scope.DefaultScopeProvider` | seam (dotted path) |
| `LIVE_ROOMS_PROVIDER` | `…live_rooms.DefaultLiveRoomsProvider` | seam (dotted path), guarded by E008 |
| `DEFAULT_ACCESS_LEVEL` | `restricted` | **axis** (public\|scope_trusted\|restricted) |
| `DEFAULT_ADMIT_REQUIRED` | `True` | **axis** (bool) |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | `""` | tuning (default provider) |
| `JOIN_TOKEN_TTL_SECONDS` | `3600` | tuning |
| `EGRESS_S3_*` | `""` | tuning (default-provider egress store) |
| `WEBHOOK_HANDLERS` | `{}` | **merge** over the builtins (`None` removes), guarded by E010; never read from env |
| `PRESENCE_SWEEP_INTERVAL_SECONDS` | `60` | tuning — also the worst-case over-count of a lost departure |
| `PRESENCE_SPAN_RETENTION_DAYS` | `400` | tuning (`None` = keep forever), guarded by W004 |
| `PRESENCE_PURGE_SCHEDULE` | `{"hour": 4, "minute": 10}` | tuning (crontab kwargs); never read from env |

`VIDEO_PROVIDER`, `DEFAULT_ACCESS_LEVEL` and `DEFAULT_ADMIT_REQUIRED` are the
three CTO-facing **config axes** (capability-config.md §16), surfaced in
`docs/capabilities.json`. They are behavioral, not gating: they change what a
room does, not which endpoints exist.

### Serializer seams (`views.py`)

`SerializerSeamMixin` — subclass a view, set `request_serializer_class` /
`response_serializer_class`, remount the URL.

### Pagination

The participants listing uses stapel-core's `AnchorPagination`
(`anchor`/`limit`/`direction`, FIFO by `joined_at`) — **limit/offset is
forbidden shelf-wide.**

### Admin categories — `@access` declarations (admin-suite AS-5)

`Room` and `RoomParticipant` are `business` (visible, staff-manageable) and
stay undecorated. `provider_room_ref` is an opaque provider room name, not a
credential — neither model is `secret` or `ops`.

`ParticipantSpan` is registered **read-only** (no add, no change, no delete):
it is a meter written by the ingest and the sweeper and summed into somebody's
invoice, so a staffer editing a row by hand is a silent restatement of a
closed period. Retention deletes spans; a person does not.

### Contract emission — the `schema` + `flows` + `errors` + `capabilities` quartet

This module emits its **own** machine-readable API contract, per-module, from a
single-module `{video + core}` Django instance mounted at the canonical
`/video/api/` prefix (`_codegen.py` / `_codegen_settings.py` /
`codegen_urls.py` / `_capabilities.py`; `make contract` / `make
contract-check`). video is **not yet mounted in stapel-example-monolith**, so
standalone validation substitutes (contract-pipeline.md §9): determinism,
self-contained `$ref` closure, JWT security on protected endpoints (the webhook
is deliberately unauthenticated — provider-signed), canonical-prefix paths.
Regenerate after any serializer/view/url/error change:

    make contract        # or: python -m stapel_video._codegen --out docs

then commit `docs/{schema,flows,errors,capabilities}.json`.

## Anti-patterns

- **Don't build a recording pipeline in this module or import a recordings
  model.** Subscribe to `video.egress_ended` — that boundary is the point.
- **Don't call a provider SDK directly.** Everything vendor-specific is behind
  the `VideoProvider` seam; the LiveKit SDK is imported lazily so the default
  path resolves without the extra.
- **Don't put workspace/org FKs on `Room`.** The scope is the opaque
  `scope_key`; resolution + membership is the `SCOPE_PROVIDER` seam.
- **Don't add limit/offset pagination.** Use the core `AnchorPagination`.
- **Don't import other stapel modules** — cross-module is comm by string name.
- **Don't bypass the settings namespace** with `os.getenv` at import time.

## App-layer override vs upstream contribution — rule of thumb

**App-layer** (host project, no fork) if the change fits a seam above: a
settings key, a subclass + URL remount, a comm subscriber, a custom provider.

**Upstream contribution** if it needs new model fields/migrations, new
endpoints, a new settings key or seam, or changes a committed schema.
