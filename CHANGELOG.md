# Changelog

All notable changes to stapel-video are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project adheres to
[Semantic Versioning](https://semver.org/).

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
