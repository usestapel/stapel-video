"""1:1 calls — one person rings one other person, and the phone rings.

A conference room and a phone call are not the same object, and this package
exists because trying to make one serve as the other is where the seams tear.
A :class:`~stapel_video.models.Room` is joined by a ``join_code``: a shareable
secret that admits whoever holds it, guarded afterwards by a lobby. That is
exactly right for "here is the link to our 3pm" and exactly wrong for "call
Anna about the bicycle" — a call has two named parties decided before anybody
connects, no third seat, nothing to share, and nothing to admit.

So a :class:`~stapel_video.calls.models.Call` writes no ``Room`` row. Its
provider room is named ``call-<id>`` and handed straight to the
``VideoProvider`` seam, which is asked for exactly two things a conference
never needed: a room capped at two participants
(:meth:`~stapel_video.providers.base.VideoProvider.ensure_call_room`) and a
grant that is explicit about what it permits
(:meth:`~stapel_video.providers.base.VideoProvider.mint_call_token`).

What this package does NOT re-implement, because the module already has it:

* **metering** — the room name is an ordinary ``room_key``, so
  ``participant_joined`` / ``participant_left`` open and close
  :class:`~stapel_video.models.ParticipantSpan` rows and every existing usage
  rollup covers calls with no new code;
* **webhook ingress** — the signature check and the merge registry in
  :mod:`stapel_video.webhooks`;
* **the socket** — ``stapel_realtime.EphemeralStreamConsumer`` and the v1
  envelope from ``stapel_core.comm.signal``;
* **the beat entry point** — :func:`stapel_video.tasks.get_video_beat_schedule`.

Read :mod:`stapel_video.calls.models` for the state machine and
:mod:`stapel_video.calls.services` for who is allowed to move it.
"""
