# Changelog

All notable changes to stapel-video are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project adheres to
[Semantic Versioning](https://semver.org/).

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
