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
- **API** — room create/info/join, participants (anchor-paginated), lobby
  admit/deny (host-only), and a signed provider webhook ingress. DTO/serializer
  seams + OpenAPI (drf-spectacular).
- **comm surface** — emits `video.egress_ended`; consumes `user.deleted` (GDPR).

## Extension points (fork-free)

### 1. Video backend — `VIDEO_PROVIDER` (dotted path, replace)

A `VideoProvider` (ABC, `providers/base.py`) is the one seam a video vendor
plugs into: `create_room` / `mint_join_token` (mandatory core) and
`start_room_egress` / `stop_room_egress` / `parse_webhook` (recording, default
`NotImplementedError` so a token-only backend stays valid). The default is
`LiveKitProvider` behind the `[livekit]` extra — its SDK is imported *lazily*
inside each method, so the default dotted path resolves on a plain install and
only calling a method without the extra raises. Resolve with
`stapel_video.providers.get_video_provider()`. Also a CTO-facing **config axis**
(which vendor carries the calls).

### 2. scope_key + membership — `SCOPE_PROVIDER` (dotted path, replace)

A `ScopeProvider` (`resolve(request) -> scope_key`, `filter(qs, request)`,
`is_member(request, scope_key) -> bool`) resolves the opaque scope and answers
the one question `scope_trusted` needs — *is this user a trusted member?* The
default is a single global scope where every authenticated user is a member; a
host returns e.g. the active `workspace_id` and real membership.

### 3. Recording hook — `video.egress_ended` (comm emit)

When a room recording finishes, the webhook path emits `video.egress_ended`
(`{egress_id, status, storage_key}`); stapel-recordings (or any subscriber)
finalizes the upload the egress wrote. **This module creates no recording
resource itself.** Schema: `schemas/emits/video.egress_ended.json`.

### Settings — `STAPEL_VIDEO` namespace (`conf.py`)

Resolution order per key: `settings.STAPEL_VIDEO[key]` -> flat Django setting ->
environment variable -> default. Read lazily at call time.

| Key | Default | Semantics |
|---|---|---|
| `VIDEO_PROVIDER` | `…livekit.LiveKitProvider` | **axis** + seam (dotted path) |
| `SCOPE_PROVIDER` | `…scope.DefaultScopeProvider` | seam (dotted path) |
| `DEFAULT_ACCESS_LEVEL` | `restricted` | **axis** (public\|scope_trusted\|restricted) |
| `DEFAULT_ADMIT_REQUIRED` | `True` | **axis** (bool) |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | `""` | tuning (default provider) |
| `JOIN_TOKEN_TTL_SECONDS` | `3600` | tuning |
| `EGRESS_S3_*` | `""` | tuning (default-provider egress store) |

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

Both models (`Room`, `RoomParticipant`) are `business` (visible,
staff-manageable) and stay undecorated. `provider_room_ref` is an opaque
provider room name, not a credential — neither model is `secret` or `ops`.

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
