"""stapel-video — video calls for the Stapel framework.

A thin, provider-agnostic library over a real-time video backend (LiveKit by
default): rooms with join codes, an access-level admission model
(public / scope-trusted / restricted lobby), a realtime waiting-room over
Channels, and a recording-egress *seam* (start/stop + a ``video.egress_ended``
comm emit) that integrates with stapel-recordings by event, never by import.
"""
