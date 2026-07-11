# CONFIG.MD — stapel-video

Config registry for **stapel-video** (`static-scaffold-and-config.md` §2). One
row per configuration key the module reads, its **source** (`env` = the process
environment / the `STAPEL_VIDEO` settings dict; `vault` = a credential that
belongs in the `stapel_core.secrets` provider seam), what it is for, whether it
is required, and its default.

Every key is read through `video_settings` (`stapel_video.conf.AppSettings`,
namespace `STAPEL_VIDEO`). Resolution order per key: `settings.STAPEL_VIDEO`
dict → a flat Django setting of the same name → environment variable → the
default below. `VIDEO_PROVIDER`, `DEFAULT_ACCESS_LEVEL` and
`DEFAULT_ADMIT_REQUIRED` are CTO-facing config axes (capability-config.md §16);
the `LIVEKIT_*` / `EGRESS_*` credentials are tuning knobs for the default
provider.

## stapel-video

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| VIDEO_PROVIDER | env | axis: dotted path to a VideoProvider — which vendor carries the calls (SDK imported lazily). | no | stapel_video.providers.livekit.LiveKitProvider |
| SCOPE_PROVIDER | env | dotted path to a ScopeProvider (scope membership for the scope_trusted auto-admit). | no | stapel_video.scope.DefaultScopeProvider |
| DEFAULT_ACCESS_LEVEL | env | axis: default room access — public \| scope_trusted \| restricted. | no | restricted |
| DEFAULT_ADMIT_REQUIRED | env | axis: whether a fresh room starts with the lobby on. | no | true |
| LIVEKIT_URL | env | LiveKit server URL (default provider). | no | (empty) |
| LIVEKIT_API_KEY | vault | LiveKit API key (default provider). | no | (empty) |
| LIVEKIT_API_SECRET | vault | LiveKit API secret (default provider). | no | (empty) |
| JOIN_TOKEN_TTL_SECONDS | env | join-token time-to-live (seconds). | no | 3600 |
| EGRESS_S3_ENDPOINT | env | recording-egress object-store endpoint (egress seam, off by default). | no | (empty) |
| EGRESS_S3_BUCKET | env | recording-egress bucket. | no | (empty) |
| EGRESS_S3_ACCESS_KEY | vault | recording-egress access key. | no | (empty) |
| EGRESS_S3_SECRET_KEY | vault | recording-egress secret key. | no | (empty) |
