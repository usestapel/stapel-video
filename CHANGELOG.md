# Changelog

All notable changes to stapel-video are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project adheres to
[Semantic Versioning](https://semver.org/).

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
