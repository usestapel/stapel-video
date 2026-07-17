# Changelog

All notable changes to stapel-video are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project adheres to
[Semantic Versioning](https://semver.org/).

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
