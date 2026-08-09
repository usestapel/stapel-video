"""In-process LiveRoomsProvider fake — what a host adopting the provider seam
writes: ~20 lines over its OWN tables, returning the refs it hands the
provider itself. Here the "table" is a class attribute a test can set."""
from stapel_video.live_rooms import LiveRoomsProvider


class HostRoomsProvider(LiveRoomsProvider):
    #: Stands in for the host's own Room rows.
    refs: list = []

    def live_rooms_for_user(self, user_id) -> list:
        return list(HostRoomsProvider.refs)
