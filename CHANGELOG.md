# Changelog

All notable changes to stapel-video are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.8.0] — 2026-08-24

### Fixed — the erasure answers its probe: stapel-video registers as a GDPR data owner

Found on a live stand: this module was a **declared** data owner that
answered no `gdpr.owner.probe`. Its `VideoGDPRProvider` was registered
in-process and its `user.deleted` handler really did erase, so a monolith
looked fine — and a fleet's owners-health said `video: alive=false` forever,
and every erasure request waited on this owner until it timed out. Liveness
is answered by the subscriber that erases, and there was none to answer.

`apps.ready()` now hands one callable to `stapel_core.gdpr.register_gdpr_owner`
and core subscribes the whole protocol: `gdpr.erasure.requested` ->
`gdpr.section.erased` (deterministic `receipt_id`, receipt emitted inside the
erase's transaction), `gdpr.owner.probe` -> `gdpr.owner.alive` from the same
module, and the deprecated `user.deleted`. **No protocol code is written
here.**

- **Owner** `video` (= `VideoGDPRProvider.section`), **subject types**
  `["account"]` — `scope_key` and `room_key` are opaque host-computed
  strings, not workspace or meeting ids a scoped erasure could match on.
- **Counts** `{rooms, participations, presence_spans}`. **The erasure
  discipline is unchanged**: rooms the subject hosted are hard-deleted
  (cascading to their participant rows), their admissions to other people's
  rooms are removed, and the meter is **pseudonymized, not deleted** —
  `presence.pseudonymize_user` rewrites `ParticipantSpan.user_id` to the
  stable keyed digest it always did, so a closed reporting period counts the
  same seconds and one subject stays one subject. `presence_spans` counts
  rows **touched**: the receipt means "made unattributable", not "deleted".
  `ParticipantSpan` semantics are untouched by this release.
- Idempotent — a redelivery receipts its zeroes (a span already carrying an
  `erased:` id is never pseudonymized twice) and mints the same `receipt_id`.
- New `stapel_video/erasure.py`: `OWNER`, `SUBJECT_TYPES`,
  `erase_subject(subject_type, subject_key, workspace_id=None)`. The
  in-process provider and all three subscribers reach this one counted
  function, so a monolith and a fleet erase the same rows the same way.

### Removed — `stapel_video.actions.handle_user_deleted`

**Breaking** (pre-1.0: minor = breaking) for anyone importing that handler
directly. The signal is still consumed — by the subscriber
`register_gdpr_owner` installs, running the same `erase_subject("account",
…)` — and it now also emits the `gdpr.section.erased` receipt the old handler
never sent. Two handlers for one signal is two erasures to keep in step.
`handle_profile_changed` is untouched.

### Changed — `stapel-core` floor raised to 0.35.0

`stapel_core.gdpr.register_gdpr_owner` exists only in 0.35.0.

### Added — comm contracts for the protocol

`schemas/consumes/gdpr.erasure.requested.json`,
`schemas/consumes/gdpr.owner.probe.json`, `schemas/emits/gdpr.owner.alive.json`,
`schemas/emits/gdpr.section.erased.json`. `schemas/consumes/user.deleted.json`
loses its `format: uuid` on `user_id` — a host's user pk is an integer as
often as a UUID, and the erasure takes both spellings as they come.

No new settings; `CONFIG.MD` is unchanged.

## [0.7.0] — 2026-08-23

### Added — the dimension the meter did not have: `scope_key`

0.6.0 could answer "how long was this person in calls" and "how long was this
room in use". It could not answer **"how long did the people in THIS workspace
talk"** — there was no tenant on a span, so a host wanting that number had two
choices, and both were bad: join the span table to its own rooms and
re-implement the union arithmetic beside the table that already owns it, or
read the instance-wide export and filter. The first produces a second answer
to "how long was this person present" that nobody reconciles against the
invoice until a customer disputes it; the second cannot be shown to a
customer at all.

**`ParticipantSpan.scope_key`** is that dimension: an opaque, host-chosen
partition key (`null`, never `""` — a host that partitions nothing writes no
scope, and an empty-string scope would be a tenant the report invented).
Migration `0003` is pure expand — one nullable column and one
`(scope_key, joined_at)` index, no data touched.

It arrives on the **join grant**, because that is the only moment the answer
is in the process: a `participant_joined` webhook names a room and a person
and never says which tenant the room belongs to, and the code that minted the
token is the one that knew. `mint_join_token(..., scope_key=...)` puts it in
the provider's per-connection metadata, the provider echoes it back on every
webhook and every roster read, and the presence writer copies it onto the
span. The sweeper's repair path reads the same echo, so a span it opens for a
lost `participant_joined` is not the one unscoped row in a tenant's month.

Only rows the ingest **creates** are stamped. The span is append-only, and a
redelivery carrying a different scope must not silently move a recorded stay
from one tenant's invoice to another's.

### Added — `manage.py video_backfill_scope --resolver <dotted path>`

History is the host's to place: a span holds an opaque `room_key`, and which
partition that room belonged to is a fact this library has never been told. So
the backfill takes the host's callable (`room_key -> scope_key | None`) and
runs it over the spans that have no scope, batched.

Idempotent and resumable by construction rather than by bookkeeping — the
population is *defined* as `scope_key IS NULL`, so every stamped row leaves
it: a crashed run resumes exactly where it stopped and a second full run does
nothing. A resolver answering `None` leaves those spans unscoped (some rooms
genuinely belong to no tenant) and the count is reported, because a resolver
pointed at the wrong table answers `None` for everything too.

### Added — `video.presence.usage_rollup` / `usage_rollup_by_month`

One partition's window, one row per person: `presence_seconds`, `rooms`,
`connections`, `first_seen`, `last_seen`. Deliberately the **same code path**
as `presence.aggregate` (`_clip` / `_merge_intervals`), because two
implementations of "how long was this person present" is two numbers.
`rooms` counts distinct `room_key`s, not spans — somebody who reconnected nine
times to one call attended one call, and the reconnects are reported
separately as `connections` so a support question has an answer in the data.

`usage_rollup_by_month(scope_key, months, tz)` cuts that into calendar months
**in the caller's zone**, newest first. Boundaries are LOCAL midnight, so a
month crossing a daylight-saving change is genuinely 743 or 745 hours: a
report titled "March" must neither swallow the hour that belongs to April nor
drop the one that belongs to March. The stored instants never move, so the
same spans re-cut into another zone's calendar without a migration. An empty
month is present with `users: []` — "no calls" and "this row failed to load"
must not look the same. `months` is clamped to 36, because the walk is linear
in every bucket and the parameter is reachable from a query string.

### Added — `GET /video/api/v1/scopes/{scope_key}/usage/`, gated on a mandate in that scope

`?months=6&tz=Europe/Berlin`, or `?month=YYYY-MM` for one. **Not `IsStaff`**:
the audience is a workspace's own owner or admin reading their own people's
minutes, and a staff gate would mean the only accounts able to see a
customer's numbers belong to the vendor.

Two gates, in order. First `HasWorkspaceMandateIfScoped` — the same core gate
stapel-calendar 0.5.0 put on its by-id reads: anonymous refused everywhere,
the guest state refused where it exists, a standalone deployment admitted, and
"could not ask" answered **503** rather than as a verdict about the caller.
Then the per-scope question, which nothing in core can answer: the caller must
hold `STAPEL_VIDEO["USAGE_MANDATE"]` (default `video.usage.read`) **in the
scope named in the URL**, resolved through the workspaces access registry.
Holding a mandate *somewhere* is not authority over a workspace id somebody
typed into a URL.

The refusal is **404, uniformly** — the same answer for a scope that does not
exist, one that belongs to somebody else, and one with no calls the caller may
not see. A 403 would confirm that a guessed tenant id is real, and the key
here *is* the host's tenant id. A scope the caller may read with no calls in
it is a 200 with empty months: once the registry has said yes, "no calls" is a
real answer and must not read as a permissions bug.

Declared `stapel_anonymous_access = ANONYMOUS_DENIED`; throttled from this
module's own namespace (`USAGE_THROTTLE`, `60/min`, scope
`video-scope-usage`) rather than by writing into the project's
`DEFAULT_THROTTLE_RATES`. Rows carry user **ids** — this library never learns
anybody's display name, and the host resolves it from the roster it has.

### Breaking (pre-1.0: minor = breaking)

- **`VideoProvider.mint_join_token` gains a `scope_key` kwarg.** An
  out-of-tree provider must accept it — the library's own call site passes it
  — and should echo it back in `parse_webhook` / `list_participants`, or every
  span that provider produces is unscoped and the usage read reports an empty
  workspace. The shipped LiveKit provider and the test fake carry it.
- **`ParticipantSpan` gains a column** (migration `0003`, additive) and
  `video.presence.spans_export` rows gain `scope_key`. A reader keyed on the
  existing fields is unaffected.
- New system check **`stapel_video.E011`**: `USAGE_AUTHORIZER` unimportable or
  not callable.

### Configuration

| Key | Default | What |
|---|---|---|
| `USAGE_MANDATE` | `video.usage.read` | capability required **in the scope asked about** |
| `USAGE_AUTHORIZER` | `…usage.staff_only_authorizer` | the gate where there are no workspaces to ask |
| `USAGE_THROTTLE` | `60/min` | rate for the `video-scope-usage` throttle scope |

The llms.txt budget rises 5000 → 6000: six new surface entries, two new comm
Functions and a new seam, raised deliberately rather than by compressing the
intent lines an agent reads to avoid rebuilding a mechanism that exists.

## [0.6.0] — 2026-08-22

### Added — presence metering: the media server stops being the only one who knows

A room was joinable, recordable and observable, and how long anybody spent in
one was recorded nowhere. Not lost — never measured. `RoomParticipant` looks
like it holds the answer and does not: it is unique per (room, user) forever,
its `joined_at` is the first knock of a lifetime, and a grace-window return
sets `left_at` back to NULL, physically destroying the interval that just
ended. State and history are different tables, and only one of them can be
summed.

**`ParticipantSpan`** is the new one: `(room_key, user_id, connection_id,
joined_at, left_at, close_reason)`, one row per connection's stay, append-only
— a closed span is never reopened or restated, so a product's grace-window
policy cannot silently rewrite a billing period. No ForeignKeys: `room_key` is
opaque (a host that kept its own Room model meters through the same table) and
`user_id` is a `CharField`, so erasure is a decision rather than a cascade.

Three writers, in descending order of trust: the provider's
`participant_joined` / `participant_left` webhooks, the sweeper, and the
host's explicit `presence.close_spans_explicitly` (a leave button). Ingest is
idempotent on `(connection_id, joined_at)` — **the provider's own join
timestamp, never our receipt time** — so at-least-once, out-of-order delivery
cannot double-count anybody, and a `participant_left` that overtakes its
`participant_joined` materializes the whole closed span by itself.

### Fixed — `parse_webhook` was throwing away everything that was not a recording

It collapsed EVERY event into four egress keys, so `participant_joined`,
`participant_left` and `room_finished` arrived at the URL, passed the
signature check, and were dropped on the floor. The media server is the only
witness of a departure that survives a closed laptop, a killed tab or a dead
network — a browser sends nothing in exactly the cases a naive meter bills
forever — and the normalizer was discarding it.

`parse_webhook` now returns the whole normalized event: `event`, `event_id`,
`event_ts`, `room`, `participant` (with the identity decomposed into
`user_id` + `connection_id` by the provider that invented the convention in
`mint_join_token`), plus the original four keys unchanged. Additive: the
recording path is byte-identical and its tests were not touched.

### Added — `WEBHOOK_HANDLERS`, a merge registry, replacing `if egress_ended`

Dispatch was a hardcoded branch, so the only way to react to a second event
was to fork the ingress or terminate the webhook in product code and
re-implement signature verification there — the same fork by two routes. It is
now the fleet's standard three-layer merge (`BUILTIN_WEBHOOK_HANDLERS` ←
`STAPEL_VIDEO["WEBHOOK_HANDLERS"]` ← `register_webhook_handler`), `None`
tombstones a builtin, and a broken entry is `stapel_video.E010` at boot rather
than a 500 on a live webhook. An event nothing handles stays a quiet 200: a
4xx would make the provider retry a delivery that was perfectly correct.

### Added — `list_participants` on the provider contract, and a sweeper that uses it

A webhook stream is at-least-once, which also means at-most-eventually: one
dropped `participant_left` is a span with no end and a number that grows
without bound, and no care in the ingest path can see a missing event. The
repair needs a second, independent reading of the room, so the reading is a
capability of the seam — a private method on one vendor's class is how a host
ends up importing the vendor SDK (SWAP004).

`presence.sweep_open_spans` confirms the connections the provider still
reports, closes the zombies **at their last confirmed moment** (bounding the
error to one sweep interval, not to however long until somebody looked), and
opens spans for live connections whose join webhook never arrived — the one
failure mode that is otherwise completely silent. `stapel_video.W003` fires
when a host drives a beat schedule with no entry for it.

### Added — three Query-Functions, raw seconds only

| Function | Answer |
|---|---|
| `video.presence.aggregate` | unioned presence seconds for one person or one room over a period |
| `video.presence.spans_export` | `{rows, cursor, total}` — the raw spans |
| `video.presence.pairs_export` | `{rows, cursor, total}` — `(user_a, user_b, room_key, co_presence_seconds)` |

- **Union, never sum.** A person on a laptop and a phone was present once.
  Billing a second device as a second human is not a number anybody can
  defend to the customer holding the invoice.
- **No threshold, anywhere.** "More than 15 minutes counts" is the consumer's
  policy and is revised per customer; an export that had already dropped the
  short overlaps could not answer the revised question. Anonymous guests are
  ordinary people here for the same reason — excluding them would price a
  product by how few accounts a customer bothers to create.
- **`{rows, cursor, total}`, never `{items}`.** Core's snapshot reader looks
  for `rows` by name; an items-shaped answer rebuilds a consumer's projection
  to EMPTY and reports success doing it.

`pairs_export` is quadratic in a room's distinct attendees (a co-presence
matrix has N²/2 cells: 435 pair evaluations for a 30-person meeting, ~125k for
a 500-person webinar). Rooms are the batch boundary, so memory stays
proportional to one room.

### Added — retention, GDPR, and the jobs that make them real

- `PRESENCE_SPAN_RETENTION_DAYS` (400 — a year of reporting plus slack),
  `manage.py video_purge_spans`, and `stapel_video.W004` when a beat schedule
  has no entry for it. Keyed on `joined_at`, so a span still open past the
  horizon goes too: that is a row the reconciler never reached, not a very
  long call. This module keeps no rollup table on purpose — the aggregate is
  computed from spans every time, which is the only way it stays correct
  while the sweeper is still closing yesterday's zombies.
- `get_video_beat_schedule()` wires both jobs; celery stays optional, and both
  are plain callables with management-command forms.
- **Erasure pseudonymizes the meter instead of deleting it.** A span holds no
  text to scrub, so what is personal about it is the `user_id` column; it
  becomes a keyed digest, which removes the person and leaves the counters,
  distinct counts and pair overlaps exactly where they were. Deleting the rows
  would silently restate closed reporting periods — and the question the
  export answers is "who was in a call during the period", not "whose account
  still exists".
- `ParticipantSpan` is read-only in the admin: a meter summed into an invoice
  is not a table a staffer edits by hand.

### Notes

Migration `0002_participantspan` is pure expand — one CREATE TABLE, no column
dropped, no row rewritten. A deployment that never turns the provider webhooks
on simply keeps an empty table. `docs/llms.txt` moves to a deliberate
5000-token budget (the stapel-calendar precedent) rather than compressing the
new surface entries into clauses.

## [0.5.1] — 2026-08-16

### Fixed — E009 measured configuration where it meant to measure a hole

0.5.0's new `stapel_video.E009` refused boot for any multi-tenant deployment
still on the shipped `DefaultScopeProvider`. It did not ask the one question
that decides whether that provider decides anything: **is this module's URL
surface mounted here?**

A host that owns its own rooms installs stapel-video for its provider seam and
its subscribers and never mounts the views. `get_scope_provider` has exactly two
call sites — `RoomCreateView.post` and `_should_auto_admit`, reached only from
`services.join_room` via `JoinView` — both behind that surface. With the surface
unmounted, nothing routes to either, and the only way to satisfy the Error is a
provider that provably never runs. meettoday's sandbox backend spent the
afternoon down on exactly this.

`check_scope_provider` now passes `surface_mounted` to core, which degrades the
finding to `stapel_video.W002` with its own sentence: configured open, consulted
by nothing today, and that changes the day you mount it. Warning rather than
silence, because a URLconf walk cannot see a host calling `services.join_room`
from its own Python.

The asymmetry was inside this file: `E008` was already gating on the same walk
and passing. It now shares the measurement instead of a copy of it.

### Changed — the URLconf walk moves to core; floor raised to 0.30.0

`_video_urls_mounted` delegates to `stapel_core.django.mounts.module_urls_mounted`.
Every module shipping a scope seam asks the same question, so the mechanism
belongs one layer down rather than re-copied per library.

## [0.5.0] — 2026-08-16

### Security — a trusted scope member is not merely an account holder

Admission was decided *after* the `Participant` row was written, so the row the
caller had just created was part of the evidence for letting them in. The row
is not the bug; consulting it first was. Admission is now decided before
anything is written, from the scope seam.

- `ScopeProvider.is_member` answers the third principal state
  (`stapel_core.django.scope`): a registered account holding no mandate
  anywhere is not a member of the room's scope. `MandateUnavailable` (503) is
  the answer for "could not find out" — a lookup that failed is not a
  membership that was proven.
- Participant identities and `scope_key` are gated behind
  `error.403.video_not_room_participant` (`ERR_403_NOT_ROOM_PARTICIPANT`).
  Who else is in the room, and which tenant the room belongs to, were readable
  by anyone who could name the room.
- New system checks for the shipped single-scope default carrying a
  multi-tenant host.

**Breaking**: a host whose provider answered `is_member` permissively, or
returned `False` where it meant "lookup failed", changes behaviour — raise
`MandateUnavailable` for the latter. Guests who could previously join a room
by holding an account are refused.

### Changed — `stapel-core` floor raised to 0.27.0

`django/scope.py` — `MandateScopeMixin` and `check_shipped_scope_provider` —
exists only in 0.27.0.

## [0.4.1] — 2026-08-15

### Changed — `stapel-core` floor raised to 0.26.0

`docs/errors.json` carries an `owner` per entry, and only stapel-core 0.26.0
emits it. The floor lagged behind, so a consumer resolving an older core
regenerated an artifact without `owner` and the drift gate went red — the
field was declared but never required. The floor now matches the artifact
that is committed.

## [0.4.0] — 2026-08-09

The seam a product can adopt without forking the provider, and the reload ghost
nobody ever catches in the act.

0.3.0 made a rename reach a live call — but only for a host that runs *this
module's* rooms, because the subscriber read this module's tables directly. A
product with its own meeting model (invitations, pin codes, calendar series)
could buy the capability and not the calling of it, which is the defect class
this whole path exists to close.

- **`LIVE_ROOMS_PROVIDER`** — new seam (`live_rooms.py`), same §8.1 dotted-path
  shape as `VIDEO_PROVIDER`/`SCOPE_PROVIDER`. Exactly one method,
  `live_rooms_for_user(user_id) -> [provider_room_ref]`; the default reads this
  module's own tables. `actions.handle_profile_changed` queries the seam
  instead of importing the models, so a host with its own rooms writes a
  ~20-line adapter and gets the rename.
- **`stapel_video.E008`** — the seam's own failure mode, closed at boot. If
  `stapel_video` is installed, the seam is at its default, and this module's
  URLs are not mounted, then nothing in that deployment can ever write what the
  default reads: the subscriber answers "no live rooms" for everybody, forever.
  Empty-because-unconfigured is indistinguishable from
  empty-because-nobody-is-on-a-call, so this is a measurement of the wiring,
  not a name check — a hard system-check error naming both remedies. A
  correctly adapted host passes without knowing it exists.
- **Stable connection identity** — `mint_join_token` takes an optional
  `client_session_id`, and the join/create endpoints, their request DTOs and
  serializers carry it end to end. Random-per-connection identity is not
  cosmetic drift: every page reload arrives at the vendor as a stranger, so the
  pre-reload connection sits there as a ghost tile until the vendor's own
  disconnect timeout — for every adopter, on every reload. Reconnecting under
  the same identity makes LiveKit evict the stale connection on sight. Callers
  that send nothing keep the random suffix, so two real devices still get two
  identities.
- **`mint_join_token` takes `user_avatar`** and the LiveKit provider always
  writes participant metadata as JSON, even when the avatar is empty — never
  "sometimes absent". `rename_participant` already echoed metadata back so a
  rename could not erase an avatar; until now nothing could set one.
- **`remove_participant`** on the contract — the kick counterpart of
  `rename_participant`, same ListParticipants + per-identity shape, and both
  now share one matcher (`{user_id}_` prefix, separator included, both identity
  forms). A rename that finds a connection a kick does not is a defect waiting
  to be read side by side.
- **`get_room_metadata` / `update_room_metadata` / `probe_reachable`** on the
  contract, implemented for LiveKit. Every provider capability a product
  actually uses now has an upstream home — which is what makes the fleet ban on
  direct `livekit` imports outside `stapel_video` (SWAP004, `stapel-swap-lint`)
  enforceable rather than aspirational.

Compatibility: `VideoProvider` subclasses outside this repo must widen
`mint_join_token` with the two new optional parameters. Everything else is
additive.

## [0.3.0] — 2026-08-09

A rename now reaches a call that is already running.

The display name travels inside the join token, so it is frozen at the instant
a connection was made. Every other reader of a name re-reads it and is correct
as soon as the write commits; a video tile keeps rendering whatever it was
handed at join. The symptom is not a broken feature — it is one person's tile
showing an old name while everyone else looks right, because everyone else
happened to reconnect after the write.

- `VideoProvider.rename_participant(provider_room_ref, user_id, user_name)` —
  new seam method, the counterpart to `mint_join_token`. Default
  `NotImplementedError` like the egress trio, so a token-only backend stays
  valid.
- `LiveKitProvider` implements it over the RoomService twirp API
  (`ListParticipants` + `UpdateParticipant`): it updates **every** connection
  the user holds in the room (one person on two devices is two identities),
  echoes the participant's metadata back so a rename cannot erase an avatar,
  and is idempotent under at-least-once redelivery.
- The module consumes **`profile.changed`** (published by stapel-profiles on
  every write to the canonical name, including the roster-side correction an
  owner makes through stapel-workspaces) and pushes the new name into every
  live room the person is in. Installing the module is enough — there is no
  host-side wiring to remember.

## [0.2.5] — 2026-08-02

Packaging/docs catch-up, no behavior change:

- CI tests the Python the stand actually runs.
- Contract documents ship in the wheel (`package-data`) (#184).
- Badge canon + Python 3.14 classifier.
- `docs/llms.txt` — the fifth contract artifact (badge-canon §3), emitted
  by `stapel_tools.llms_txt` and checked by the `make contract-check`
  drift gate.

## [0.2.3] — 2026-07-17

Fix-up #2: 0.2.2's regen still baked the old version into
`docs/capabilities.json` (`make contract` ran before the version bump
landed). Re-ran with 0.2.3 already in `pyproject.toml`; verified match,
suite green.

## [0.2.2] — 2026-07-17

Fix-up: 0.2.1's CI/publish failed on contract drift — `docs/capabilities.json`
embeds the package version and wasn't regenerated for the 0.2.1 bump.
Regenerated via `make contract`; no other diff.

## [0.2.1] — 2026-07-17

Fleet follow-up to stapel-core 0.12.0 (legacy shim sweep). No source
changes needed. Full suite green against core 0.12.0.

### Changed
- `stapel-core` dependency ceiling `<0.12` → `<0.13`.

## [0.2.0] — 2026-07-17

### Removed
- Deprecated `default_app_config` marker (and its `__all__` export) from
  `stapel_video/__init__.py` — obsolete since Django 3.2, removed in Django 4.0;
  `VideoConfig` is auto-discovered from `apps.py`.

## [0.1.2] — 2026-07-17

### Changed
- `stapel-core` ceiling raised `>=0.10,<0.11` → `>=0.10,<0.12` (core 0.11
  fleet re-pin: default bus, nav, config-checks, error params/language —
  additive for modules). Contract artifacts regenerated (version bump);
  suite green with `livekit`/`channels` extras installed.

## [0.1.0] — 2026-07-10

Initial alpha.

### Added
- `Room` / `RoomParticipant` models with `abc-defg-hij` join codes, an
  access-level admission model (`public` / `scope_trusted` / `restricted`) and a
  lobby switch.
- `VideoProvider` seam (ABC) with a lazy-loading `LiveKitProvider` behind the
  `[livekit]` extra: mint join token, create room, start/stop recording egress,
  verify webhook.
- REST API: room create/info/join, anchor-paginated participants, host lobby
  admit/deny, and a signed provider webhook ingress.
- Realtime lobby `LobbyConsumer` over Channels (optional `[channels]` extra) on
  the stapel-core JWT channels middleware.
- Recording *seam*: `video.egress_ended` comm emit (no pipeline, no
  stapel-recordings import).
- `SCOPE_PROVIDER` seam (scope_key resolution + `scope_trusted` membership).
- Contract quartet (`docs/{schema,flows,errors,capabilities}.json`) with three
  CTO-facing axes: `VIDEO_PROVIDER`, `DEFAULT_ACCESS_LEVEL`,
  `DEFAULT_ADMIT_REQUIRED`.
- GDPR: `user.deleted` erases created rooms and participations.
